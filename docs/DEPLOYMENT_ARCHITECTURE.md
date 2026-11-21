# ASEA Hybrid Deployment Architecture

## Goal

Keep the browser dashboard and job coordination in the cloud while running repository access, repair execution, tests, local Ollama inference, and Docker-based validation on the user's Windows machine.

The deployment target is a zero-cost prototype using provider free tiers where available. Free-tier availability, limits, and terms can change; this architecture must not assume uninterrupted hosting or guaranteed free usage.

## Components

- **Frontend:** Next.js dashboard hosted on Vercel.
- **Cloud API:** FastAPI service hosted on Render. It authenticates browser requests, stores job metadata, exposes job status and review APIs, and coordinates worker leases.
- **Database:** Managed PostgreSQL (initial candidate: Supabase) for durable job metadata, worker registration/leases, review decisions, and event history.
- **Local worker:** A Windows process started by the user. It makes outbound HTTPS requests to the cloud API, claims jobs, runs the existing ASEA workflow locally, and sends progress/results back.
- **Local execution dependencies:** Ollama, Docker Desktop, Git, and the worker's local workspace.
- **Secrets:** GitHub credentials and Ollama configuration remain on the worker machine. The browser must never receive worker credentials.

## Job lifecycle

1. Browser submits an analysis or repair request to the cloud API.
2. Cloud API validates the request, creates a durable job record, and queues it.
3. An authenticated worker claims the job using a short-lived lease.
4. Worker clones or analyzes the repository locally and runs the existing ASEA graph.
5. Worker sends bounded progress updates and state transitions to the cloud API.
6. When a patch requires human review, the cloud API records the review request and exposes it to the dashboard. The worker pauses and releases its execution slot.
7. User approves or rejects the patch in the dashboard. The cloud API records the decision; the worker resumes the same workflow from a durable checkpoint.
8. Worker runs final validation and, only when explicitly requested and approved by the existing workflow, publishes a GitHub pull request.
9. Cloud API stores the terminal result and makes it available to the dashboard.

## Security requirements

- Worker communication is outbound-only over HTTPS; do not expose Ollama, Docker, or a listening worker port to the public internet.
- Authenticate workers independently from browser users. Worker credentials must be revocable and stored locally, not embedded in frontend code.
- Use short-lived job leases with heartbeats and idempotent claim/complete operations so disconnects do not silently duplicate work.
- Authorize every job, review, and result operation against its owning account. Do not rely on an opaque job ID as authorization.
- Never send GitHub tokens, local filesystem paths, environment variables, or raw secrets to the browser or cloud logs.
- Keep repository cloning, patch application, Docker execution, and GitHub publishing on the local worker.
- Preserve the existing human patch-review gate, isolated test execution, final validation, and opt-in publishing behavior.
- Apply request limits, strict CORS, TLS, secret redaction, and bounded payload sizes at the cloud API and ingress.
- Treat worker-reported results as untrusted input; validate schemas and enforce legal state transitions server-side.

## Persistence and recovery

- PostgreSQL is the source of truth for cloud job metadata, ownership, worker leases, review decisions, and event history.
- LangGraph checkpoints and repository workspaces remain local to the worker initially. The worker must persist checkpoints on disk and recover paused review workflows after restart.
- A worker restart must re-register, renew its lease, and safely resume or release work. Expired leases must become eligible for reassignment only after the server determines whether the previous attempt can be resumed safely.
- Do not deploy the current SQLite files on an ephemeral cloud filesystem as production persistence.

## Implementation sequence

1. Extract the existing workflow execution and review-resume logic from FastAPI route handlers into a reusable execution service without changing behavior.
2. Add a local worker process with authenticated registration, polling/claiming, heartbeats, progress reporting, and graceful shutdown.
3. Add cloud-side worker/job coordination and ownership-aware APIs.
4. Introduce PostgreSQL persistence and migrations for jobs, accounts, workers, leases, and events; keep local SQLite support for development during migration.
5. Update the dashboard to authenticate users and access only their own jobs.
6. Add integration tests for worker disconnects, expired leases, duplicate messages, review pause/resume, and publishing safeguards.
7. Deploy the API, database, and frontend; then test a full repair using the local worker.

## Explicit non-goals for the first deployment

- No public inbound connection to the user's PC.
- No cloud-hosted Ollama inference.
- No automatic merge of generated pull requests.
- No claim that a sleeping or unavailable free-tier service provides production-grade availability.
- No multi-user deployment until authentication, tenant isolation, and authorization are implemented and tested.


## Worker protocol

The initial Supabase PostgreSQL schema is documented in [POSTGRES_PERSISTENCE.md](POSTGRES_PERSISTENCE.md) and defined in `migrations/postgres/0001_cloud_coordination.sql`. It is schema-only; the API still uses local SQLite until the PostgreSQL adapter and transactional worker protocol are implemented. The detailed enrollment, authentication, lease, fencing, review-resume, and recovery contract is documented in [WORKER_PROTOCOL.md](WORKER_PROTOCOL.md). Implement and test that protocol before enabling distributed worker endpoints or deploying the cloud API.
