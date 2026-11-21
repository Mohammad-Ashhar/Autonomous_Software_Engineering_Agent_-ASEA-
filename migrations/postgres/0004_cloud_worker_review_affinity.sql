-- Keep a cloud repair job on the worker that owns its local workspace.
-- This prevents a second machine from resuming a checkpoint containing paths
-- that only exist on the original worker.
ALTER TABLE public.asea_repair_jobs
    ADD COLUMN IF NOT EXISTS execution_worker_id UUID
    REFERENCES public.asea_workers(worker_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_asea_jobs_execution_worker_queue
    ON public.asea_repair_jobs(execution_worker_id, created_at, job_id)
    WHERE status = 'queued';
