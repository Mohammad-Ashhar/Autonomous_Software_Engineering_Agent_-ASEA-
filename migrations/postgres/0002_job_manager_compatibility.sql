-- Add fields required by the existing repair-history/publishing workflow.
ALTER TABLE public.asea_repair_jobs
    ADD COLUMN IF NOT EXISTS publishing_retry BOOLEAN NOT NULL DEFAULT FALSE;
