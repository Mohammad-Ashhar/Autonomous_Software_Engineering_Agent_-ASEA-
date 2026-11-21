-- ASEA cloud coordination schema for Supabase PostgreSQL.
-- Apply only to a Supabase project whose Auth schema is enabled.
-- This migration is additive. It does not migrate or delete local SQLite data.

CREATE TABLE IF NOT EXISTS public.asea_workers (
    worker_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    credential_hash BYTEA NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'revoked')),
    worker_version TEXT,
    capabilities JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(capabilities) = 'object'),
    last_heartbeat_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ,
    CONSTRAINT asea_workers_revocation_consistency CHECK (
        (status = 'active' AND revoked_at IS NULL)
        OR (status = 'revoked' AND revoked_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_asea_workers_owner_status
    ON public.asea_workers(owner_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS public.asea_worker_enrollment_codes (
    enrollment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    code_hash BYTEA NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_asea_enrollment_owner_expiry
    ON public.asea_worker_enrollment_codes(owner_id, expires_at)
    WHERE consumed_at IS NULL;

CREATE TABLE IF NOT EXISTS public.asea_repair_jobs (
    job_id TEXT PRIMARY KEY CHECK (length(job_id) BETWEEN 1 AND 128),
    owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    repository TEXT NOT NULL CHECK (length(repository) BETWEEN 1 AND 2048),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN (
            'queued', 'running', 'waiting_for_review', 'completed', 'failed'
        )),
    message TEXT NOT NULL DEFAULT 'Repair job created.',
    publish_requested BOOLEAN NOT NULL DEFAULT FALSE,
    review_request JSONB,
    result JSONB,
    error JSONB,
    review_decision TEXT CHECK (
        review_decision IS NULL OR review_decision IN ('approved', 'rejected')
    ),
    review_feedback TEXT CHECK (
        review_feedback IS NULL OR length(review_feedback) <= 2000
    ),
    workflow_version TEXT NOT NULL DEFAULT '1',
    checkpoint_reference JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_asea_jobs_owner_created
    ON public.asea_repair_jobs(owner_id, created_at DESC, job_id DESC);
CREATE INDEX IF NOT EXISTS idx_asea_jobs_owner_status_created
    ON public.asea_repair_jobs(owner_id, status, created_at DESC, job_id DESC);
CREATE INDEX IF NOT EXISTS idx_asea_jobs_claim_queue
    ON public.asea_repair_jobs(created_at, job_id)
    WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS idx_asea_jobs_terminal_retention
    ON public.asea_repair_jobs(status, updated_at)
    WHERE status IN ('completed', 'failed');

CREATE TABLE IF NOT EXISTS public.asea_job_leases (
    lease_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id TEXT NOT NULL REFERENCES public.asea_repair_jobs(job_id)
        ON DELETE CASCADE,
    worker_id UUID NOT NULL REFERENCES public.asea_workers(worker_id)
        ON DELETE CASCADE,
    fencing_generation BIGINT NOT NULL CHECK (fencing_generation > 0),
    issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    released_at TIMESTAMPTZ,
    CONSTRAINT asea_lease_expiry_after_issue CHECK (expires_at > issued_at),
    CONSTRAINT asea_lease_release_after_issue CHECK (
        released_at IS NULL OR released_at >= issued_at
    )
);

-- A job can have only one current lease. Expired leases remain recorded until
-- an explicit recovery transition releases them.
CREATE UNIQUE INDEX IF NOT EXISTS idx_asea_one_active_lease_per_job
    ON public.asea_job_leases(job_id)
    WHERE released_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_asea_leases_worker_expiry
    ON public.asea_job_leases(worker_id, expires_at)
    WHERE released_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_asea_leases_expiry
    ON public.asea_job_leases(expires_at)
    WHERE released_at IS NULL;

CREATE TABLE IF NOT EXISTS public.asea_job_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL REFERENCES public.asea_repair_jobs(job_id)
        ON DELETE CASCADE,
    worker_id UUID REFERENCES public.asea_workers(worker_id)
        ON DELETE SET NULL,
    lease_id UUID REFERENCES public.asea_job_leases(lease_id)
        ON DELETE SET NULL,
    event_type TEXT NOT NULL CHECK (length(event_type) BETWEEN 1 AND 80),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(payload) = 'object'),
    idempotency_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT asea_event_idempotency_key_length CHECK (
        idempotency_key IS NULL OR length(idempotency_key) BETWEEN 1 AND 200
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_asea_event_worker_idempotency
    ON public.asea_job_events(worker_id, idempotency_key)
    WHERE worker_id IS NOT NULL AND idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_asea_events_job_order
    ON public.asea_job_events(job_id, event_id);
CREATE INDEX IF NOT EXISTS idx_asea_events_owner_created
    ON public.asea_job_events(owner_id, created_at DESC);

-- Enable RLS now so the tables are not accidentally exposed if Supabase's
-- Data API is enabled. Service-role/backend connections bypass RLS; API
-- authorization must still enforce owner_id and worker ownership.
ALTER TABLE public.asea_workers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.asea_worker_enrollment_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.asea_repair_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.asea_job_leases ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.asea_job_events ENABLE ROW LEVEL SECURITY;

-- No browser-facing policies are intentionally created. All access goes
-- through the authenticated ASEA backend, which applies explicit ownership
-- checks. Do not expose these tables directly to anon/authenticated clients.
