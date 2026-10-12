# Step 9 Report — Task and Workspace Binding

## Request flow inspected

The browser sends the textarea value and selected filename as `{task, filename}`. Previously the page prefilled a division-by-zero prompt and auto-selected `repair_loop_temp.py`; the API passed those values to the agent, whose generic “program exited successfully” check could accept an unrelated program. The dashboard also lacked task-specific acceptance criteria and did not show the actual submitted request or task workspace.

## Changes

- The task field now starts blank and the file chooser has an explicit placeholder. The browser no longer auto-selects a demo file or inserts a demo request.
- The API accepts only task/file pairs with a matching local acceptance profile: factorial with `factorial.py`, or division-by-zero with `repair_loop_temp.py`. Unsupported requests and mismatches are rejected before a task or agent is created.
- Every accepted request creates a fresh `TaskWorkspaceManager` workspace, unique task-state directory, agent instance, and artifact ID. The exact submitted text and filename are passed to `ReActAgent.run`. The request digest and the agent’s `run_python` invocation are checked before the result is eligible for success.
- A factorial request gets a task-local `factorial.py` fixture containing the factorial function and in-file checks for 0, 1, and 5. After the agent run, an independent acceptance script in a separate isolated directory imports the resulting code and verifies `factorial(0) == 1`, `factorial(1) == 1`, and `factorial(5) == 120` through the Docker executor.
- Division-by-zero requests get a separate Docker acceptance run checking a clear zero-division result and normal division. Docker failure, timeout, wrong output, request mismatch, or cleanup failure prevents success and patch creation.
- Successful artifacts record the exact request, selected file, dashboard task ID, executed workspace, and verified criteria. Diff, reject, and promotion endpoints require that task’s own verified acceptance state; promotion still requires explicit human approval.
- The dashboard shows the actual request, selected file, executed workspace path, and acceptance profile/result. Starting a new request clears the previous result and review controls.
- The benchmark runner now seeds the declared division bug inside each isolated BENCH-002 and BENCH-004 workspace. This keeps benchmark behavior deterministic when the user’s primary `workspace/repair_loop_temp.py` has already been fixed.

## Files changed

- `web/app.py`
- `web/static/app.js`
- `web/static/index.html`
- `web/static/styles.css`
- `tests/test_web_app.py`
- `benchmarks/runner.py`
- `tests/test_benchmark_runner.py`
- `README.md`
- `STEP9_REPORT.md`

The pre-existing working-tree modification to `workspace/repair_loop_temp.py` was preserved and not edited by this step.

## Verification

- Focused dashboard and benchmark-runner tests: `venv\Scripts\python.exe -m unittest tests.test_web_app tests.test_benchmark_runner -q` — **16 passed**.
- Full suite: `venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider` — **88 passed, 1 skipped, 9 subtests passed**.
- Syntax: `venv\Scripts\python.exe -m compileall -q agent llm tools benchmarks tests web` — passed.
- Ruff on changed Python files: `venv\Scripts\ruff.exe check web/app.py benchmarks/runner.py tests/test_web_app.py tests/test_benchmark_runner.py` — passed.
- Real dashboard service integration with Docker and mock model: factorial and division-by-zero requests both completed successfully in distinct temporary workspaces, each with its matching verified acceptance profile and `docker-sandbox` execution.
- Four Docker benchmarks: `venv\Scripts\python.exe benchmarks\runner.py --output "$env:TEMP\codepilot-step9-benchmarks-final.json"` — BENCH-001 through BENCH-004 **PASSED**; 0 failed, 0 blocked, 0 timed out. The runner used disposable scenario workspaces. No containers remained afterward.
- The initial benchmark attempt against the already-modified primary workspace had BENCH-002 fail because its input was already repaired. The runner was corrected to seed the expected buggy fixture only in its isolated benchmark copy; the user’s workspace file remains untouched. The rerun above passed all scenarios.
- `git diff --check` — passed. No commit or push was made.

## Remaining limits

- The local mock demo supports the two task/file pairs listed above. Other requests are rejected until a matching acceptance profile is added.
- The displayed task workspace is temporary and is removed after its result and patch artifact are recorded. The path shown is the actual workspace used for execution.
- Mock mode does not demonstrate live Bedrock behavior. Docker remains required for both agent execution and acceptance verification.
