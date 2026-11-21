# Cloud Worker Client Setup

## Current delivery scope

The cloud-worker HTTPS client and enrollment commands are available. This is
only the secure transport/enrollment foundation: it does not yet poll, claim,
or execute repair jobs. Continue using the existing local SQLite worker until
the cloud execution runtime and review checkpoint handoff are delivered.

## Install the optional worker dependencies

From the ASEA repository root:

```powershell
python -m pip install -e ".[cloud-worker]"
```

The optional `keyring` dependency stores the worker credential in the operating
system credential vault. ASEA does not write the worker secret to a plaintext
configuration file or print it to the terminal.

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
