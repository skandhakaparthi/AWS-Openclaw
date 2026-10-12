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

## Benchmark scenarios and outcome

| Scenario | Behavior exercised | Result |
|---|---|---|
| BENCH-001 | Execute an already-correct program | Blocked: Docker unavailable |
| BENCH-002 | Generate and apply a division-by-zero repair, then re-execute | Blocked: Docker unavailable |
| BENCH-003 | Refuse an unknown-name repair and contain changes in an isolated workspace | Blocked: Docker unavailable |
| BENCH-004 | Repeat an equivalent runtime error and stop at the repeat limit | Blocked: Docker unavailable |

The machine-readable run is saved at [benchmarks/step3_results.json](benchmarks/step3_results.json). Actual totals: 0 passed, 0 failed, 4 infrastructure-blocked, 0 timed out. The runner exited 1 as required. It did not switch to local execution. Since Docker was unavailable, the scenario-level agent behavior could not be verified end-to-end in this run.

## Verification performed

- Baseline before edits: `venv\Scripts\python.exe -m pytest tests -q` — 29 passed and 8 subtests passed. Pytest emitted a cache warning because the existing `.pytest_cache` directory could not be accessed.
- Final tests: `venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider` — **44 passed, 8 subtests passed**.
- Benchmark: `venv\Scripts\python.exe benchmarks\runner.py --output benchmarks\step3_results.json` — valid JSON written; exit code **1** because all four scenarios were blocked by Docker unavailability.
- Docker availability probe: daemon unavailable; sandbox image unavailable. No Docker integration scenario could run.
- `git diff --check` — passed (Git printed only line-ending normalization notices).
- No AWS credentials, paid Bedrock calls, AWS resources, deployments, pushes, commits, history rewrites, resets, or primary-tree rollback operations were performed.

## Remaining gaps and Step 4 instructions

1. Start Docker Desktop and build the sandbox image from the repository root: `docker build -f sandbox/Dockerfile -t codepilot-sandbox .`.
2. Run `venv\Scripts\python.exe benchmarks\runner.py --output benchmarks\step3_results.json` and inspect every scenario outcome. A successful harness run requires real sandbox execution; mock LLM behavior remains a deterministic demo.
3. If using Bedrock in a later step, add provider usage accounting and provider request timeouts before claiming the $0.05 cost or end-to-end 60-second limits cover model calls. Do not invoke paid APIs without explicit authorization.
4. Decide how reviewed successful task workspace changes should be promoted to the primary workspace. Current behavior intentionally leaves them isolated under `.agent_tasks`.
5. Replace or explicitly deprecate the legacy destructive `tools.git_manager.rollback()` API before any other caller is allowed to use it on a primary repository.

## Git status

Changes are left uncommitted. Although the tests pass, the required real benchmark scenarios could not execute because Docker is unavailable, so the Step 3 completion criteria are not fully met. Existing commits and tags were preserved.
