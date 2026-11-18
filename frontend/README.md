# ASEA Web Interface

The frontend is a Next.js application for interacting with the ASEA FastAPI backend. It provides repository analysis, repair-job submission and status, patch review, and repair history.

## Requirements

- Node.js 22 or newer
- npm
- A running ASEA backend (see the root [README](../README.md))

## Local development

From this directory:

```bash
npm ci
npm run dev
```

Open http://localhost:3000. The frontend uses http://127.0.0.1:8000 as its default API base URL.

Start the backend separately from the repository root:

```bash
python -m asea.api.main
```

The backend must be ready before submitting analysis or repair jobs. The frontend displays a connection error if the API cannot be reached.

## Configure the backend URL

Set `NEXT_PUBLIC_API_URL` in a frontend `.env.local` file when the backend is not running at the default address:

```dotenv
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
```

Restart the Next.js development server after changing environment variables. This variable is public and is embedded in browser-side code; never put API keys, GitHub tokens, or other secrets in a `NEXT_PUBLIC_*` variable.

## Quality checks and production build

Run the checks used by CI:

```bash
npm run lint
npm run build
```

Run the production build locally:

```bash
npm run start
```

The production server listens on port 3000 by default. Configure the API URL at build time using `NEXT_PUBLIC_API_URL`.

## Operational notes

- The backend allows browser origins `http://localhost:3000` and `http://127.0.0.1:3000` by default. Update the backend CORS configuration when deploying the frontend elsewhere.
- The API's production-mode shared Bearer key is intended for trusted server-side clients. Do not expose it in browser code; use an authenticated reverse proxy or identity-aware gateway for a public deployment.
- Repair is supervised: inspect proposed changes and explicitly approve or reject patches when the workflow pauses for review.
