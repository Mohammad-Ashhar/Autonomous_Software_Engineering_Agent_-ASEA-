-- Indexes required by the cloud worker credential and single-job protocol.
-- Apply after 0001_cloud_coordination.sql and 0002_job_manager_compatibility.sql.

CREATE UNIQUE INDEX IF NOT EXISTS idx_asea_workers_credential_hash
    ON public.asea_workers(credential_hash);

-- The initial worker runtime processes one job at a time. An expired lease
-- remains active until an explicit recovery/revocation transition releases it.
CREATE UNIQUE INDEX IF NOT EXISTS idx_asea_one_active_lease_per_worker
    ON public.asea_job_leases(worker_id)
    WHERE released_at IS NULL;
