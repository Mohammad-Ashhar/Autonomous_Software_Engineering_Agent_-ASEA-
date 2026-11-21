# PostgreSQL Cloud Persistence Schema

## Status

The migration in `migrations/postgres/0001_cloud_coordination.sql` creates the first cloud coordination schema for Supabase PostgreSQL. It is additive and does not change the local SQLite job manager or connect the API to PostgreSQL yet.

## Apply to a Supabase project

1. Create a Supabase project and keep its database credentials private.
2. Open the project's SQL Editor.
3. Review and execute `migrations/postgres/0001_cloud_coordination.sql`.
4. Confirm the five `public.asea_*` tables and their indexes exist.
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

- Existing local jobs and LangGraph checkpoints remain in local SQLite files; this migration does not copy them to PostgreSQL.
- The current local worker still uses the shared local SQLite queue. Do not point it at a cloud API or run multiple workers yet.
- No distributed worker endpoints are enabled by this migration.
- PostgreSQL adapter, transactional claim/heartbeat/recovery operations, and cloud-worker transport must be implemented and tested before deployment.
