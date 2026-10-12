# STEP 3 — Self-Correction Loop and Benchmark Harness

Date: 2026-10-12  
Branch: `master`  
Starting HEAD: `1ef5c61c131eae6b5b4112ce937776b7bfc917c7`  
Starting tree: clean; branch was three commits ahead of `origin/master`.

## Existing behavior found

- `ReActAgent` planned with `TaskPlanner`, executed one target file per iteration through `ToolRegistry`, and reflected on process output. It treated any `success: true` as task success, called the LLM on Docker errors, accepted a successful write response without reading the file back, and used a root Git tag followed by `git reset --hard` for rollback.
- `TaskState` stored a simple status, iteration count, cost, and history.
- `SafetyManager` already configured 5 steps, 60 seconds, a $0.05 budget, and a repeated-error limit of 2. The LLM client does not expose token usage, so reliable model cost measurement is unavailable.
- `ToolRegistry` exposed only read, write, and run tools. The executor defaulted to Docker and failed closed. Its explicit `local-unsandboxed` mode remains an operator-controlled opt-in.
- `benchmarks/runner.py` displayed the suite rather than running scenarios. Existing scenario definitions describe execution, division-by-zero repair, safe refusal, and runaway stopping.

## Changes

- `agent/react_agent.py` classifies execution outcomes before any repair call. Docker/image/container failures stop as `INFRA_ERROR`; path/mode denials stop as `PERMISSION_ERROR`; timeouts stop as `TIMEOUT`; syntax errors, assertion failures, and runtime errors retain separate classifications. The original structured executor result, including stderr, is saved in task state.
- An agent success now requires a structured execution result with `success: true` and exit code 0. Missing results, nonzero exits, and model responses cannot produce success. Writes are read back and compared with the proposed source before the loop treats them as applied. Repeated identical errors stop on the configured second occurrence, and the existing 5-iteration and 60-second checks remain in use.
- `TaskState` now includes the request, plan, current step, inspected/changed files, tool calls, latest execution/test results, error classification/signatures, final summary, and rollback field. Cost remains unknown (`null`); the report and state explicitly say cost enforcement is unavailable because usage data is not measured. The prior behavior of adding a fake `$0.00` cost per call was removed.
- Agent runs now copy the repository workspace into `.agent_tasks/<task-id>/workspace` and point the permission-gated registry there. The sandbox mounts only this selected workspace read-only. The agent no longer creates a repository-root checkpoint or calls `reset --hard`. Failed task copies are retained for diagnosis and are isolated from the primary workspace; they are not represented as rolled back.
- `tools/filesystem.py`, `tools/tool_registry.py`, and `tools/executor.py` accept a trusted, constructor/API-provided workspace root. Path containment checks are relative to that root. The model still supplies only a filename; execution still routes through the same executor and honors sandbox-required mode.
- `benchmarks/runner.py` now supports `--scenario` and `--output`, creates a fresh temporary workspace for every selected scenario, drives the actual agent and tool chain in mock LLM mode, writes JSON results, and exits nonzero on failure, infrastructure block, or timeout. BENCH-004 supplies a deterministic no-op repair proposal to exercise repeated-error stopping. Mock mode is marked as demonstration, not evidence of live model capability.
- Added `tests/test_react_agent.py` and `tests/test_benchmark_runner.py`; added `.agent_tasks/` to `.gitignore`.

## State machine and stopping rules

`PLAN → ACT → OBSERVE → REFLECT` continues after a verified tool-mediated fix. Reflection succeeds only for an executed, zero-exit acceptance run. Infrastructure, permission, and timeout results stop before asking the LLM to repair. Source errors can be analyzed; equivalent repeated error output stops at the existing repeat limit. The safety manager stops after 5 iterations or when its 60-second check is exceeded. Exceptions are stored as failed task outcomes rather than converted to success.

The current task interface uses successful execution of the target program as its acceptance check; it does not yet support a separate arbitrary test command or task-specific assertion schema. Thus a zero exit verifies execution, not every possible semantic requirement.

## Isolation and rollback limits

- The agent works on a copied task workspace and never applies changes to the primary repository. Benchmark scenarios use disposable `TemporaryDirectory` workspaces that are removed after each scenario.
- The isolated-workspace write test confirms writes go to the injected workspace while the primary fixture remains byte-for-byte unchanged. The executor still rejects traversal and has no fallback when Docker is absent.
- No reset-based rollback is used by `ReActAgent`; `rollback_occurred` is false in summaries. Isolation contains failed edits, but retained `.agent_tasks` directories are not automatically discarded. They can be inspected and removed by the user.
- `tools/git_manager.py` retains its legacy `rollback()` implementation using `git reset --hard` for compatibility. The agent does not call it. This standalone API remains unsafe if another caller points it at a valuable repository; this step did not migrate or invoke that API. Existing tests exercise it only in temporary repositories.
- The change uses copied workspace directories rather than Git worktrees. It isolates agent edits but does not automatically merge successful changes into the primary workspace.

## Benchmark scenarios and outcome (Step 3A live run)

| Scenario | Behavior exercised | Result |
|---|---|---|
| BENCH-001 | Execute an already-correct program | Passed: Docker output `120`, exit 0 |
| BENCH-002 | Detect division by zero, repair the source, and re-execute | Passed: repair recorded, expected output, exit 0 |
| BENCH-003 | Refuse an unknown-name repair and verify task checkpoint state | Passed: `NameError` observed, repair refused, task rollback verified |
| BENCH-004 | Repeat an equivalent runtime error and stop at the repeat limit | Passed: stopped after 2 iterations, task rollback verified |

The machine-readable run is saved at [benchmarks/step3_results.json](benchmarks/step3_results.json). Step 3A supersedes the earlier blocked run: 4 passed, 0 failed, 0 infrastructure-blocked, 0 timed out; runner exit code **0**. Execution used `docker-sandbox` for each scenario. The deterministic mock LLM was used for planning/repair decisions; it did not replace execution. BENCH-003 and BENCH-004 verified that task-owned file state matched the initial checkpoint state. BENCH-004's proposed repair was a no-op; no files required restoration. Temporary benchmark workspaces were deleted after each run.

## Verification performed

- Baseline before edits: `venv\Scripts\python.exe -m pytest tests -q` — 29 passed and 8 subtests passed. Pytest emitted a cache warning because the existing `.pytest_cache` directory could not be accessed.
- Final tests after Step 3A rollback checks: `venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider` — **47 passed, 8 subtests passed**.
- Benchmark: `venv\Scripts\python.exe benchmarks\runner.py --output benchmarks\step3_results.json` — valid JSON written; exit code **0**, all four scenarios passed.
- Docker: client/server version **29.8.2**, `desktop-linux` daemon available. The CLI was installed at `C:\Users\skand\AppData\Local\Programs\DockerDesktop\resources\bin` but absent from the agent process PATH; Docker was added only to the PowerShell process PATH for the checks and benchmark run. No persistent PATH or execution-mode change was made.
- Image inspection: `codepilot-sandbox:latest` existed with image ID `sha256:eb8957b6985a45ac1174d9be83550afbf44834b206814460a29767ebda0257da`, configured user `sandboxuser`. Build context files were inspected; rebuilding was unnecessary.
- Live sandbox probe: success, exit code 0, output captured. In-container UID 1000; network connection attempt failed; AWS credential paths and `/var/run/docker.sock` absent; `/workspace` mount was read-only.
- Timeout probe: a 30-second harmless sleep was stopped at the 2-second executor timeout and returned `timed_out: true`, return code `-1`. The container list was empty after cleanup.
- Missing-image behavior was checked with a temporary nonexistent image tag and returned `image-missing`, with no code execution. Docker-unavailable behavior was checked by forcing the availability check false and making any call to local execution raise; it returned `docker-unavailable` and did not attempt local execution. Existing unit tests also cover these fail-closed cases.
- `git diff --check` — passed (Git printed only line-ending normalization notices).
- No AWS credentials, paid Bedrock calls, AWS resources, deployments, pushes, commits, history rewrites, resets, or primary-tree rollback operations were performed.

## Remaining gaps and Step 4 instructions

1. If Docker is later stopped, start Docker Desktop and wait for `docker info` to show a `Server` section before rerunning the harness. The current image is already present.
2. If using Bedrock in a later step, add provider usage accounting and provider request timeouts before claiming the $0.05 cost or end-to-end 60-second limits cover model calls. Do not invoke paid APIs without explicit authorization.
3. Decide how reviewed successful task workspace changes should be promoted to the primary workspace. Current behavior intentionally leaves them isolated under `.agent_tasks`.
4. The legacy `tools.git_manager.rollback()` still uses `git reset --hard`; the agent no longer calls it. Replace or explicitly deprecate that API before another caller uses it on a primary repository.

## Git status

At Step 3A start, HEAD was `1ef5c61` and the Step 3 changes were uncommitted. At final inspection, HEAD was `96b56cad2ffcb193b4409103d365de7963f3729a` (`feat: Implement self-correction loop and benchmark harness`), recorded in the reflog at 2026-10-12 16:20 +0530. No commit command was issued during this Step 3A work. The commit appeared while the task was in progress; it was preserved without reset or history edits. The branch is now four commits ahead of `origin/master`. Step 3A report, agent rollback, runner acceptance, JSON results, and regression-test updates remain uncommitted. Nothing was pushed. Step 3A verified the four scenarios through Docker; model cost measurement remains unavailable.
