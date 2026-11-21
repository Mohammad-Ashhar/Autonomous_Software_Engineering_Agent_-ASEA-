# PostgreSQL Cloud Persistence Schema

## Status

The migrations in `migrations/postgres/` create the cloud coordination schema for Supabase PostgreSQL. The PostgreSQL job manager, worker protocol, and LangGraph checkpoint adapter use this schema when `ASEA_DATABASE_URL` is configured. Local SQLite remains available for development.

## Apply to a Supabase project

1. Create a Supabase project and keep its database credentials private.
2. Open the project's SQL Editor.
3. Review and execute every migration in `migrations/postgres/` in numeric order (`0001` onward).
4. Confirm the `public.asea_*` tables, columns, constraints, and indexes exist.
5. Do not add browser-facing RLS policies or expose these tables through the Supabase Data API.

The schema references `auth.users`, so it is intended for a Supabase PostgreSQL database with Supabase Auth enabled. It uses RLS with no client policies: the ASEA backend will be the only application access path and must still enforce ownership on every operation. Use a backend-only database connection; never put a database password or service-role key in a `NEXT_PUBLIC_*` variable.

## Tables

- `asea_repair_jobs`: owner-scoped jobs and review/result state.
- `asea_workers`: independently authenticated, revocable local workers.
- `asea_worker_enrollment_codes`: single-use, expiring enrollment-code hashes.
- `asea_job_leases`: lease identity, expiry, and fencing generation. A partial unique index prevents two active leases for one job.
- `asea_job_events`: append-only progress and idempotency records.

The indexes support owner-scoped history, queued-job selection, lease-expiry scans, and duplicate event rejection.

## Important limitations

- Existing local jobs and local LangGraph checkpoints are not migrated automatically to PostgreSQL.
- The legacy `ASEA_EXECUTION_MODE=worker` mode still uses a shared local SQLite queue. Do not point it at a cloud API.
- The separate `asea cloud-worker run` command uses the PostgreSQL-backed cloud queue and checkpointer; it requires the `cloud-worker` extra and an enrolled worker credential.
- A live Supabase deployment and full end-to-end worker/review validation are still required before production use.
