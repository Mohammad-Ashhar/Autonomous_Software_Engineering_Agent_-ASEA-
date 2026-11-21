# ASEA Cloud Deployment Checklist

This checklist deploys the cloud dashboard and coordination API while keeping
repository cloning, Ollama inference, Docker validation, and GitHub publishing
on the user's Windows worker. It does not move local execution into Render.

The repository includes a Render Blueprint at `render.yaml`. The frontend is
deployed separately to Vercel from the `frontend` directory.

## 1. Create Supabase project

1. Create a Supabase project and keep its project URL and anon/public key.
2. Open the SQL Editor.
3. Run the PostgreSQL migrations in numeric order, exactly once:
   - `migrations/postgres/0001_cloud_coordination.sql`
   - `migrations/postgres/0002_job_manager_compatibility.sql`
   - `migrations/postgres/0003_cloud_worker_protocol_indexes.sql`
   - `migrations/postgres/0004_cloud_worker_review_affinity.sql`
4. Copy the Session pooler connection string for the project. Use the
   connection string format supplied by Supabase and require TLS with
   `sslmode=require`. Do not commit the connection string or put it in Vercel.

## 2. Deploy the API to Render

1. In Render, create a Blueprint deployment from this repository and select
   the checked-in `render.yaml`.
2. Confirm the service is named `asea-api` and uses the free plan if it is
   available for the account and region.
3. Set the secret environment values when Render prompts for them:
   - `ASEA_DATABASE_URL`: Supabase PostgreSQL Session pooler connection URL,
     including `sslmode=require`.
   - `SUPABASE_URL`: the HTTPS Supabase project URL, without a path.
   - `SUPABASE_ANON_KEY`: the Supabase anon/public key.
   - `ASEA_WORKER_CREDENTIAL_PEPPER`: generate and set this before deployment;
     the API now refuses PostgreSQL cloud mode without it.
   - `ASEA_CORS_ORIGINS`: the exact Vercel production origin, for example
     `https://your-project.vercel.app` (no path and no trailing slash).
   - `ASEA_WORKER_CREDENTIAL_PEPPER`: a private random value of at least 32
     characters, used to hash enrolled worker credentials. Generate one locally
     with `python -c "import secrets; print(secrets.token_urlsafe(48))"` and
     save it only as a Render secret. Never commit it or reuse a public key.
4. Deploy and wait for the Render health check at `/ready` to pass.
5. Record the public API origin, for example
   `https://asea-api.onrender.com`.

The blueprint sets `ASEA_ENV=production` and
`ASEA_EXECUTION_MODE=api`. Production startup requires Supabase Auth
configuration. The API uses PostgreSQL when `ASEA_DATABASE_URL` is set;
do not attach a persistent-workflow expectation to Render's local filesystem.

## 3. Deploy the frontend to Vercel

1. Import the same GitHub repository into Vercel.
2. Set the project Root Directory to `frontend`.
3. Add these environment variables for Production (and Preview only if you
   intentionally want preview deployments connected to the same backend):
   - `NEXT_PUBLIC_API_URL`: the Render API origin, with no trailing slash.
   - `NEXT_PUBLIC_SUPABASE_URL`: the HTTPS Supabase project URL.
   - `NEXT_PUBLIC_SUPABASE_ANON_KEY`: the Supabase anon/public key.
4. Deploy or redeploy the frontend.
5. Copy the exact production Vercel origin into Render's
   `ASEA_CORS_ORIGINS` value and redeploy the API if it changed.

Only the Supabase anon/public key belongs in frontend configuration. Never
place the database password, Supabase service-role key, worker credential,
GitHub token, or any other secret in a `NEXT_PUBLIC_*` variable.

## 4. Verify the cloud services

- Open `<API_ORIGIN>/health`; expect HTTP 200 and `status: healthy`.
- Open `<API_ORIGIN>/ready`; expect HTTP 200 and `status: ready`.
- Open the Vercel site and verify the sign-up/sign-in screen appears.
- Create a test account and verify sign-in.
- Confirm an unauthenticated request to a protected API route is rejected.
- Confirm an authenticated user can create and view only their own jobs.
- Enroll the local worker and verify its status becomes active.
- Submit a small test repair and verify queueing, worker execution, review
  pause/resume, final result, and event history before relying on the service.

Do not consider deployment complete merely because the frontend loads or the
API health endpoint responds. A full repair must be tested with the local
worker, real Supabase database, and review flow.

## 5. Start the local Windows worker

Follow [CLOUD_WORKER_SETUP.md](CLOUD_WORKER_SETUP.md) for the cloud-worker
extra, enrollment, credential storage, and run command. The worker needs its
own access to the Supabase PostgreSQL connection for durable LangGraph
checkpoints, in addition to the cloud API URL. Keep the worker's repository
workspace available while a job is paused for review.

## Free-tier limitations

This is a prototype configuration, not a production availability guarantee.
Render's free web service may sleep when idle and has limited memory and
ephemeral local storage. Supabase free projects may pause under inactivity or
quota rules. Provider pricing, limits, eligibility, and terms can change.
Check the current provider dashboards before deployment; do not add paid
resources without reviewing the cost first.
