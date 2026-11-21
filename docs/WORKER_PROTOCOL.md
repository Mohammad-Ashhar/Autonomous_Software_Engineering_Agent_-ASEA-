# Cloud Worker Registration and Job-Lease Protocol

## Status and scope

This document specifies the distributed-worker protocol for the hybrid ASEA deployment. The PostgreSQL worker credential and lease API is being delivered incrementally. The current `ASEA_EXECUTION_MODE=worker` runtime still uses a shared local SQLite database and is not compatible with a cloud API on another machine.

The first distributed release remains single-user until browser authentication and account ownership are implemented. Do not expose worker enrollment or job APIs publicly before those prerequisites and the persistence migration are complete.

## Implementation status\n\nThe API foundation now provides one-time enrollment-code issuance and exchange, per-worker HMAC-hashed bearer credentials, owner-scoped worker listing/revocation, worker authentication, worker heartbeat, PostgreSQL queue claims with fencing generations, lease renewal, idempotent bounded event insertion, and fenced completion/failure endpoints. Expired leases are conservatively finalized as failed before a subsequent claim; jobs are never blindly replayed. The cloud worker runtime, safe review checkpoint handoff, and end-to-end Supabase integration are not yet implemented. Do not deploy or connect a worker until those remaining stages and database-backed integration tests are complete.\n\nRequired backend settings are `ASEA_DATABASE_URL` and a randomly generated `ASEA_WORKER_CREDENTIAL_PEPPER` of at least 32 characters. Keep the pepper server-side and stable; changing it invalidates all enrolled worker credentials. Enrollment codes expire after 10 minutes and are single-use. Worker secrets are returned only once by the enrollment exchange.\n\n### Implemented API routes

- User-authenticated: `POST /v1/workers/enrollment-code`, `GET /v1/workers`, and `POST /v1/workers/{worker_id}/revoke`.
- Worker-authenticated: `POST /v1/worker/enroll`, `POST /v1/worker/heartbeat`, `POST /v1/worker/jobs/claim`, `POST /v1/worker/jobs/{job_id}/heartbeat`, and `POST /v1/worker/jobs/{job_id}/events`, `POST /v1/worker/jobs/{job_id}/complete`, and `POST /v1/worker/jobs/{job_id}/fail`.

Apply migrations `0001_cloud_coordination.sql`, `0002_job_manager_compatibility.sql`, and `0003_cloud_worker_protocol_indexes.sql` in order before using these routes. The enrollment-code and worker-secret hashes use HMAC-SHA256 with the server-only pepper. A worker may hold one active lease at a time. Expired running leases are finalized as failed on the next claim attempt. This deliberately avoids automatic replay of potentially side-effecting local work. A user must submit a fresh repair job after inspecting the failure.

## Trust boundaries

- The browser authenticates as a user. It never receives a worker credential.
- A Windows worker authenticates independently with a per-worker credential.
- The cloud API is authoritative for user ownership, job state, worker state, lease expiry, and review decisions.
- The worker is trusted to execute locally but its progress and result payloads are untrusted input. The API validates schemas, size limits, ownership, lease identity, and legal state transitions.
- The worker makes outbound HTTPS requests only. No inbound port, public Ollama endpoint, or remote Docker endpoint is required.
- GitHub tokens, Ollama endpoints/keys, local paths, environment variables, and checkpoint contents stay on the worker.

## Enrollment and credential lifecycle

1. An authenticated user requests enrollment from the dashboard. Enrollment is unavailable until user authentication and ownership checks exist.
2. The API creates a cryptographically random, single-use enrollment code with a short expiry (recommended: 10 minutes), binds it to the requesting account, and stores only a keyed hash.
3. The user enters the code into the local worker setup command. The worker exchanges it over HTTPS for a worker ID and a high-entropy worker secret.
4. The API stores only a keyed hash of the worker secret. The worker writes the secret to the user's local OS-protected secret store where supported; it must not print the secret or place it in source control or logs.
5. The worker secret is scoped to one worker and one account. It cannot call browser/user endpoints or access another account's jobs.
6. Users can list and revoke their workers. Revocation takes effect immediately; the API rejects subsequent requests from that worker.
7. Credential rotation creates a new secret and invalidates the old one. Enrollment codes and worker secrets are never returned again after issuance.

Use a password-hashing or keyed-HMAC construction appropriate for high-entropy random secrets, constant-time verification, TLS, rate limits, and redacted logs. Never use a user-supplied worker name or ID as proof of identity.

## Worker API contract

All endpoints are HTTPS and require a valid worker bearer credential. JSON bodies have strict schemas and bounded sizes. Every request includes a unique request/idempotency key where the operation can be retried.

| Endpoint | Purpose |
|---|---|
| `POST /v1/workers/enroll` | Exchange a single-use enrollment code for worker credentials. |
| `POST /v1/workers/heartbeat` | Report liveness, worker version, capabilities, and active lease ID. |
| `POST /v1/workers/jobs/claim` | Atomically claim one eligible queued job. Empty queue returns no job. |
| `POST /v1/workers/jobs/{job_id}/heartbeat` | Renew the lease for the matching active job. |
| `POST /v1/workers/jobs/{job_id}/events` | Append bounded progress/status events for the matching lease. |
| `POST /v1/workers/jobs/{job_id}/complete` | Finalize a successful `COMPLETED` workflow result under the current lease. |
| `POST /v1/workers/jobs/{job_id}/fail` | Finalize a failed job with bounded diagnostics under the current lease. |
| `POST /v1/workers/jobs/{job_id}/review-wait` | Persist a review checkpoint reference and release the execution lease. |
| `POST /v1/workers/jobs/{job_id}/resume` | Fetch a pending review decision for the paused job and acquire a new lease. |
| `POST /v1/workers/jobs/{job_id}/complete` | Submit a validated terminal outcome for the matching lease. |
| `POST /v1/workers/jobs/{job_id}/fail` | Report a failure for the matching lease with redacted, bounded diagnostics. |

The API returns only the repository locator and explicit job options required to execute the job. It does not return cloud credentials or other users' data.

## Lease and concurrency rules

- Initial defaults: 90-second lease, heartbeat every 20 seconds. These are configuration defaults, not guarantees; network latency and execution scheduling must be considered.
- Claim is a single database transaction. PostgreSQL selects eligible queued work with row locking (for example, `FOR UPDATE SKIP LOCKED`), creates a fresh lease ID and monotonically increasing fencing generation, and changes the job to running before returning it.
- A job can have at most one current lease. The server never trusts a worker-provided timestamp to decide whether a lease is valid; it uses server time.
- Heartbeats renew only when worker ID, job ID, lease ID, fencing generation, and non-expired lease all match.
- Progress, review-wait, completion, and failure writes require the same current lease tuple. Stale workers receive a conflict and must stop acting on that job.
- Terminal completion/failure requests are idempotent per worker and idempotency key. A retried request with the same key returns the original terminal event rather than creating a second terminal transition. Claim idempotency is not yet implemented.
- A worker must stop execution when it learns that its lease was revoked, expired, or superseded. It must not publish a PR after losing its lease.
- A worker claims one job at a time initially. Do not run multiple workers against the current SQLite queue.

## Job and review state transitions

Allowed server-side transitions:

- `queued -> leased/running`: successful atomic claim.
- `running -> running`: heartbeat or progress event under the current lease.
- `running -> waiting_for_review`: persist checkpoint reference and release lease.
- `waiting_for_review -> queued`: user decision recorded; job becomes eligible for a worker to resume.
- `queued -> leased/running`: a worker claims the resumed job and receives the stored decision plus checkpoint reference.
- `running -> completed|failed`: terminal result accepted under the current lease.

The user review decision is stored by the cloud API and is immutable for that review request. A worker cannot approve its own patch. The worker resumes the existing LangGraph checkpoint locally and acknowledges consumption of the decision. Review-paused jobs do not hold a worker lease, so the PC can be offline while a user reviews a patch.

## Disconnect, expiry, and recovery

- A missed heartbeat does not immediately reassign work. The lease first expires according to server time.
- On expiry, the next claim request marks the running job failed and releases its lease. It is not blindly replayed: cloning, patch application, tests, and GitHub publishing may have side effects.
- The next worker may resume only when the server has a safe recovery record and the worker confirms the referenced local checkpoint exists and matches the job/workflow version.
- If the checkpoint is absent or inconsistent, the job is marked failed with a retry-from-scratch recommendation; the API must not pretend the old execution resumed.
- Completion is accepted only from the current lease generation. A delayed completion from an old worker cannot overwrite a newer attempt.
- Worker shutdown should stop claiming new work, finish or checkpoint the current safe operation, and release the lease when safe. Forced shutdown relies on expiry/recovery.

## PostgreSQL records

Minimum logical records (exact physical schema is finalized with the migration):

- `users`: authenticated account identity.
- `workers`: owner, worker ID, credential hash, status, last heartbeat, version/capabilities, revoked timestamp.
- `worker_enrollment_codes`: owner, keyed code hash, expiry, consumed timestamp.
- `repair_jobs`: owner, repository locator, publish flag, state, result/error, timestamps, workflow version.
- `job_leases`: job, worker, lease ID, fencing generation, issued/expiry timestamps, released timestamp.
- `job_events`: append-only event ID, owner/job, worker/lease identity, event type, bounded payload, timestamp, idempotency key.

Indexes are required for eligible queue claims, owner-scoped job listing, worker heartbeat lookup, lease expiry scans, and idempotency-key uniqueness. Use database transactions and constraints for state changes; do not implement distributed claims with process-local locks.

## Operational and security requirements

- Enforce per-user active-job limits and per-worker concurrency limits server-side.
- Apply request rate limits and payload size limits to enrollment, heartbeat, event, and result endpoints.
- Redact authorization headers, enrollment codes, worker secrets, GitHub tokens, and provider keys from logs and error responses.
- Validate repository URLs using the existing repository validation rules. Never accept a cloud-supplied local filesystem path.
- Bound event frequency, event payload size, result size, and retained event history.
- Keep the browser's job queries and review actions scoped to the authenticated owner.
- Do not deploy multi-user access until authorization tests prove cross-account job, worker, event, and review isolation.

## Delivery sequence

1. Implement user authentication/ownership and PostgreSQL migrations for jobs and worker credentials.
2. Implement one-time enrollment, per-worker authentication, revocation, and rotation.
3. Implement transactional claims, leases, fencing generations, heartbeat, idempotent events, and fenced terminal completion/failure.
4. Adapt the worker to HTTPS transport behind a separate execution mode; retain the current local SQLite mode for development.
5. Add safe checkpoint handoff for review pause/resume and a worker-side recovery strategy.
6. Add integration tests against PostgreSQL for concurrent claims, duplicate requests, stale leases, revocation, worker disconnect/restart, review pause/resume, and stale completion.
7. Only then deploy the cloud API and connect the dashboard.

## Acceptance criteria

- Two workers cannot claim the same job concurrently.
- A stale or revoked worker cannot heartbeat, report progress, resume review, complete, fail, or publish for a job.
- A retried claim or event request cannot create duplicate work or duplicate events.
- A job can wait for human review while the worker is offline, then resume from the correct local checkpoint after a new valid lease is acquired.
- A worker can be revoked without changing browser user credentials.
- No local worker service, Ollama port, Docker socket, GitHub token, or local path is exposed to the public internet.
- All job and worker operations are owner-scoped and tested before multi-user release.
