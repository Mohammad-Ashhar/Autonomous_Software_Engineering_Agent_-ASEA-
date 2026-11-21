# PostgreSQL Job Manager Adapter

The opt-in `PostgresRepairJobManager` implements the existing repair-job storage
interface against the Supabase PostgreSQL schema. It verifies that the schema
migrations have been applied and uses PostgreSQL transactions for global job
admission and `FOR UPDATE SKIP LOCKED` for queue claiming.

Apply migrations in order:

1. `0001_cloud_coordination.sql`
2. `0002_job_manager_compatibility.sql`

The adapter is not selected by the API automatically. It is intentionally
isolated until the cloud-worker execution mode, worker authentication, leases,
and recovery protocol are implemented. Do not configure the production API to
use it yet. Local SQLite remains the active backend and the existing worker
workflow is unchanged.

Set `ASEA_DATABASE_URL` only in a backend secret store when the cloud runtime
is ready. The URL must use PostgreSQL with `sslmode=require` or stronger.
Never commit it or expose it through a frontend `NEXT_PUBLIC_*` variable.
