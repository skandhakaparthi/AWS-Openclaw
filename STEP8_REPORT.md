# Step 8 Report — Local Demonstration Web Interface

## Architecture and files

The dashboard is a small standard-library HTTP application in `web/app.py` with static HTML, CSS, and JavaScript in `web/static/`. It delegates task work to the existing `ReActAgent`, which retains its task-workspace lifecycle, `ToolRegistry`, safety checks, Docker executor, and review artifact generation. It uses no new package dependencies.

- `web/app.py`: localhost-only API, bounded in-memory task tracking, mock-mode agent factory, polling, diff retrieval, and explicit promotion/rejection endpoints.
- `web/static/index.html`, `styles.css`, `app.js`: responsive dark developer dashboard with task progress, plan, structured events, Docker/result state, changed files, diff, and approval dialog.
- `tests/test_web_app.py`: local API and promotion regression coverage using temporary workspaces.
- `README.md`: local demo setup instructions.

## Install and start

The existing project environment already contains the required dependencies. From the repository root on Windows:

```powershell
.\venv\Scripts\python.exe -m web.app
```

Open http://127.0.0.1:8765. The dashboard explicitly runs in mock model mode and makes no AWS calls. Docker must be available and the sandbox image built for code execution:

```powershell
docker build -f sandbox/Dockerfile -t codepilot-sandbox .
```

Select a Python file, enter the task, and run. Review the diff after a verified success, then explicitly approve promotion or reject the patch. Stop the server with Ctrl+C.

## Safety controls

- The server binds only to `127.0.0.1` (or the equivalent loopback hostname). API requests must come from a loopback client and use the matching local Host; writes additionally require a local Origin, same-origin fetch metadata when present, and an unpredictable CSRF token.
- Only top-level Python files in the configured demo workspace can be selected. The API has no shell, Docker management, arbitrary file read, or arbitrary file write endpoint.
- Request and task sizes, tracked task count, concurrent runs, history fields, and agent console capture are bounded. One task can execute at a time.
- The existing agent and fail-closed Docker executor remain the code execution route. Dashboard success requires the existing verified acceptance result plus successful `docker-sandbox` execution. Missing Docker or image, failures, and timeouts cannot be reported as success.
- A diff is available only for a successful review artifact. Promotion requires the explicit `approved: true` action and uses the existing recoverable patch-promotion API. Rejection leaves the primary workspace untouched.
- Browser output is inserted as text, not HTML. Responses set a restrictive Content Security Policy, disable caching, and do not return environment variables or credentials.

## Verification performed

- Focused API tests: `venv\Scripts\python.exe -m unittest tests.test_web_app -v` — **8 passed**.
- Full suite: `venv\Scripts\python.exe -m pytest -q` — **83 passed, 1 skipped, 9 subtests passed**.
- Ruff: `venv\Scripts\ruff.exe check web tests/test_web_app.py` — passed.
- Docker daemon: `docker info` — available (Docker Desktop, Linux engine).
- Sandbox image: `docker image inspect codepilot-sandbox` — available; configured user is `sandboxuser`.
- Startup smoke check: launched `venv\Scripts\python.exe -m web.app`; `/` returned HTTP 200 and `/api/health` reported `llm_mode=mock`, Docker available, and image available. Server was stopped after the check.
- Benchmarks: `venv\Scripts\python.exe -m benchmarks.runner --output "$env:TEMP\codepilot-step8-benchmarks.json"` — **BENCH-001 through BENCH-004 all PASSED; 0 failed, 0 blocked, 0 timed out**. The benchmark runner used temporary workspaces and mock LLM mode. `docker ps --no-trunc` showed no remaining containers after execution.
- `git diff --check` passed. No commit, push, AWS configuration, Bedrock call, or deployment was performed.

The test suite and benchmarks above exercise the backend and agent. A GitHub Actions run is unrelated to this local UI work; no claim is made about a hosted Actions result.

## Known limitations

- Task status and logs live in process memory and are lost when the local server exits.
- The dashboard intentionally permits one active task at a time and exposes only top-level Python demo files.
- Mock mode is deterministic demo behavior, not evidence of live model capability. Docker sandbox execution is still required to run code.
- Patch promotion retains the existing Step 6 process-level recovery guarantees and crash-atomicity limitation.
- The interface is designed for a local demonstration, not as a remotely hosted multi-user service.
