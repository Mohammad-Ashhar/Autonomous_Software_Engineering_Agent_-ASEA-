# Cloud Worker Client Setup

## Current delivery scope

The cloud-worker HTTPS client, enrollment commands, and durable PostgreSQL
LangGraph checkpoint adapter are available. The cloud API now supports checkpoint-backed human-review handoff. The worker
polling/execution loop is still a separate delivery; continue using the existing
local SQLite worker until that runtime is available.

## Install the optional worker dependencies

From the ASEA repository root:

```powershell
python -m pip install -e ".[cloud-worker]"
```

The optional `keyring` dependency stores the worker credential in the operating
system credential vault. The PostgreSQL checkpoint extra lets the upcoming
cloud runtime persist LangGraph state in the same Supabase database used for
job coordination. ASEA does not write the worker secret to a plaintext
configuration file or print it to the terminal.

The worker will use `ASEA_DATABASE_URL` with `sslmode=require` (or stronger).
The first cloud-worker runtime startup will initialize LangGraph checkpoint
tables through the official PostgreSQL checkpointer. Keep the database URL in
the worker machine environment; never put it in the repository or dashboard.

## Enroll this machine

Generate a one-time enrollment code from the authenticated ASEA dashboard/API,
then run:

```powershell
asea cloud-worker enroll --api-url https://YOUR-ASEA-API.example.com
```

Enter the enrollment code at the hidden prompt. The API returns a worker ID and
a high-entropy secret; the secret is stored in the OS credential vault. The
code is single-use and expires after ten minutes.

Check local enrollment:

```powershell
asea cloud-worker status --api-url https://YOUR-ASEA-API.example.com
```

Use the same normalized API URL for status and later runtime commands. If the
credential vault is unavailable or locked, enrollment fails rather than
falling back to plaintext storage.

## Security notes

- Only HTTPS API URLs are accepted.
- Worker credentials are sent only in the Authorization header.
- Enrollment codes and worker secrets are not included in logs or status output.
- The browser's Supabase access token is not used by the worker.
- Revoking the worker in the dashboard invalidates its credential.
- A cloud repair job is pinned to the first worker that claims it. This keeps
  its local repository workspace on the same machine when it resumes after
  human review. A job paused for review releases its lease; submitting a
  decision queues it for that same enrolled worker to resume.
