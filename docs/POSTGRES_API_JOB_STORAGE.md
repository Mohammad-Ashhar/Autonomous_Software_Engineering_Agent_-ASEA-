# PostgreSQL-backed API job storage

The API selects its repair-job backend from configuration:

- Without `ASEA_DATABASE_URL`, ASEA uses the existing local SQLite job manager.
- With `ASEA_DATABASE_URL`, the API uses `PostgresRepairJobManager` and the
  Supabase PostgreSQL tables. The URL must use PostgreSQL and require TLS
  (`sslmode=require`, `verify-ca`, or `verify-full`).

The PostgreSQL backend is initialized lazily and validates the existing schema.
It does not create or alter database tables. Apply migrations in order:

1. `migrations/postgres/0001_cloud_coordination.sql`
2. `migrations/postgres/0002_job_manager_compatibility.sql`
3. `migrations/postgres/0003_cloud_worker_protocol_indexes.sql`

Install the API extra so the `psycopg` driver is available.

## Cloud API behavior in this stage

When PostgreSQL storage is enabled, the API:

- creates repair jobs in PostgreSQL with the authenticated Supabase owner ID;
- lists and retrieves jobs from PostgreSQL with owner scoping;
- leaves queued jobs available for the authenticated cloud-worker claim API;
- does not start a local LangGraph execution or initialize a local SQLite
  checkpoint database;
- checks PostgreSQL connectivity during startup/readiness.

The cloud API must not be configured with
`ASEA_EXECUTION_MODE=worker`. That mode is reserved for the existing local
SQLite worker. A future HTTPS-connected Windows worker uses the cloud worker
protocol instead.

Cloud review handoff and publishing retries intentionally return HTTP 409
until the cloud job lifecycle/checkpoint handoff stage is implemented. This
prevents a decision from being accepted and then stranded without a worker
resume path. Cloud worker completion/failure transitions and lease recovery
are also not part of this change.

## Local development remains unchanged

Do not set `ASEA_DATABASE_URL` for the existing local setup. The current
SQLite API and local worker continue sharing their local job/checkpoint files.
No SQLite data is copied to PostgreSQL and no migration is performed
automatically.
