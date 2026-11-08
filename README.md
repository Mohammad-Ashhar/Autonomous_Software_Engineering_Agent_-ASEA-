# ASEA — Autonomous Software Engineering Agent

ASEA is a repository analysis and repair workflow built around specialized agents. It inventories a repository, parses supported source languages, looks for evidence-backed issues, proposes patches, requests human approval, and validates accepted changes in an isolated test environment.

## Current language capabilities

| Language | Repository inventory | Syntax / symbol parsing | AI issue analysis | Patch generation and test validation |
|---|---:|---:|---:|---:|
| Python | Yes | Yes | Yes | Yes |
| JavaScript | Yes | Yes | Yes | Not enabled |
| TypeScript / TSX | Yes | Yes | Yes | Not enabled |
| Other detected languages | Yes | Not yet | Not yet | Not enabled |

ASEA deliberately separates *language detection*, *parsing*, *analysis*, and *repair support*. JavaScript and TypeScript findings are not sent into the repair loop yet: ASEA does not currently have a matching isolated test runner for those ecosystems. This prevents a generated patch from being labelled safe without appropriate validation.

## How it works

1. **Repository inventory** — identifies source files, language distribution, and common dependency manifests without executing repository code.
2. **Language adapters** — resolve each source file to a parser and report normalized functions, classes, imports, and syntax status.
3. **Issue analysis** — asks the configured local LLM to report evidence-backed findings. Analysis failures are surfaced separately from successful zero-finding results.
4. **Prioritization** — sends only findings in languages with enabled repair adapters into the repair workflow. Other findings remain visible as repair-blocked findings.
5. **Patch generation and review** — generates a focused patch and pauses for human approval.
6. **Validation** — applies the patch to a candidate copy and runs tests in the ASEA Docker sandbox before preserving accepted changes.

## Requirements

- Python 3.12+
- Docker Engine / Docker Desktop
- Ollama-compatible local LLM endpoint
- Node.js is not required for the currently enabled Python repair workflow

Install development and API dependencies:

```bash
python -m pip install -e ".[dev,api]"
```

Build the Python sandbox image:

```bash
docker build -t asea-sandbox:py312 -f docker/Dockerfile .
```

Configure the LLM endpoint and model using the environment variables documented in the project configuration. Do not commit API keys or local credentials.

## Run the API

```bash
python -m uvicorn asea.api.main:app --host 127.0.0.1 --port 8000
```

The frontend is in `frontend/`. Configure `NEXT_PUBLIC_API_URL` to point to the API, then run the frontend's development script from that directory.

## Safety and current limitations

- Repository source is treated as untrusted input; analysis does not execute it.
- Test execution runs in a network-disabled, read-only Docker container with resource limits.
- Dependency installation is not performed automatically as part of repository analysis.
- Human approval is required before a generated patch is validated and preserved.
- Python is the only language with end-to-end patch validation enabled today.
- Static/LLM findings are not a guarantee that every defect will be found. A completed analysis means the configured analyzers completed; it is not a proof of correctness.
- Language support is expanded only after parsing, detection, patch validation, and ecosystem-specific test execution are covered by tests.

## Development checks

```bash
ruff check .
python -m compileall -q asea
pytest -q
```

ASEA is under active development. Treat the capability table above as the source of truth for what the current version can safely do.
