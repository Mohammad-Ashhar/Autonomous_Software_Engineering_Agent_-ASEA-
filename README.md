# ASEA — Autonomous Software Engineering Agent

ASEA is a repository analysis and repair workflow built around specialized agents. It inventories a repository, parses supported source languages, looks for evidence-backed issues, proposes patches, requests human approval, and validates accepted changes in isolated test environments.

## Current language capabilities

| Language | Repository inventory | Syntax / symbol parsing | AI issue analysis | Patch generation and test validation |
|---|---:|---:|---:|---:|
| Python | Yes | Yes | Yes | Yes |
| Plain JavaScript (.js, .mjs, .cjs) | Yes | Yes | Yes | Yes, with Node's built-in test runner |
| JSX | Yes | Yes | Yes | Not enabled |
| TypeScript / TSX | Yes | Yes | Yes | Not enabled |
| Other detected languages | Yes | Not yet | Not yet | Not enabled |

JavaScript repair is intentionally limited to dependency-free Node.js projects. A project may have a `package.json`, but ASEA does not install npm dependencies or execute arbitrary package scripts. If dependencies are declared, a non-native test script is configured, or npm lifecycle hooks are present, validation is marked unsupported and automated repair is blocked.

## How it works

1. **Repository inventory** — identifies source files, language distribution, and common dependency manifests without executing repository code.
2. **Language adapters** — resolve each source file to a parser and report normalized functions, classes, imports, and syntax status.
3. **Issue analysis** — asks the configured local LLM to report evidence-backed findings. Analysis failures are surfaced separately from successful zero-finding results.
4. **Baseline tests** — selects the isolated Python or Node.js runner. JavaScript repairs require a passing baseline test run.
5. **Prioritization** — sends only findings with an enabled repair adapter and a supported test environment into the repair workflow.
6. **Patch generation and review** — generates a focused patch and pauses for human approval.
7. **Validation** — applies the patch to a candidate copy, runs syntax checks and tests in the matching Docker sandbox, and preserves only validated changes.

## JavaScript / npm safety policy

- ASEA reads `package.json` as data; it does not run `npm install`, `npm ci`, or package lifecycle hooks.
- Declared `dependencies`, `devDependencies`, `optionalDependencies`, or `peerDependencies` cause the Node runner to return an explicit unsupported status.
- If a package test script exists, only the exact `node --test` command is accepted. ASEA invokes Node directly; it does not invoke npm scripts.
- Repositories without a package manifest can use Node's native test discovery.
- Test execution uses a network-disabled, read-only, unprivileged Docker container with CPU, memory, PID, and time limits.
- Jest, Vitest, Mocha, third-party dependencies, JSX, TypeScript, and mixed Python/JavaScript repair are not enabled yet.

This is a deliberate first step toward safe npm support, not general npm compatibility. A project that requires dependencies or a third-party framework must be reported as unsupported rather than treated as successfully tested.

## Requirements

- Python 3.12+
- Docker Engine / Docker Desktop
- Ollama-compatible local LLM endpoint
- Node.js is not required on the host; the Node sandbox image supplies Node.js 22.

Install development and API dependencies:

```bash
python -m pip install -e ".[dev,api]"
```

Build both isolated execution images:

```bash
docker build -t asea-sandbox:py312 -f Dockerfile.sandbox .
docker build -t asea-sandbox:node22 -f Dockerfile.sandbox.node .
```

Configure the LLM endpoint and model using the environment variables documented in the project configuration. Do not commit API keys or local credentials.

## Run the API

```bash
python -m uvicorn asea.api.main:app --host 127.0.0.1 --port 8000
```

The frontend is in `frontend/`. Configure `NEXT_PUBLIC_API_URL` to point to the API, then run the frontend's development script from that directory.

## Safety and current limitations

- Repository source is treated as untrusted input; analysis does not execute it.
- Test execution runs in network-disabled, read-only Docker containers with resource limits.
- Dependencies are never installed automatically.
- Human approval is required before a generated patch is validated and preserved.
- A passing test suite is evidence of validation, not a proof of correctness.
- Static/LLM findings are not a guarantee that every defect will be found.
- Language support is expanded only after parsing, detection, patch validation, and ecosystem-specific test execution are covered by tests.

## Development checks

```bash
ruff check asea tests
python -m compileall -q asea tests
pytest -q
```

ASEA is under active development. Treat the capability table and the explicit unsupported statuses as the source of truth for what the current version can safely do.
