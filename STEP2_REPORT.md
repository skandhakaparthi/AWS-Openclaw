# STEP2_REPORT.md — Secure Docker Sandbox Implementation Report

Project: CodePilot-AI (AWS-Openclaw)
Date: 2026-10-12
Base: Step 1 report commit `f107ccb` · Implementation commit: **`793b97b`** ("Implement fail-closed Docker sandbox with hardened execution") · Report committed on top of it. No push, no reset, no history rewrite, no tags touched.

---

## 1. Files changed

| File | Change |
|---|---|
| `tools/executor.py` | Rewritten: fail-closed execution modes, hardened `docker run` command, container cleanup, workspace path containment, structured results with `timed_out` |
| `sandbox/Dockerfile` | Removed `pip install` of boto3/pytest (sandbox scripts are stdlib-only), non-root user hardened, inert default `CMD`, build instructions documented |
| `.dockerignore` | **New** — excludes `.git/`, `venv/`, caches, `task_state/`, `*.md`, `aws/` from the build context |
| `tools/git_manager.py` | Backward-compatible optional `cwd=None` parameter on `run_git_command`, `create_checkpoint`, `rollback`, `get_current_commit` (so tests target temp repos; default behavior unchanged) |
| `README.md` | Development Mode paragraph updated to describe fail-closed behavior and image build command |
| `tests/test_executor.py` | **New** — 19 mocked unit tests (fail-closed, hardening, cleanup, bypass prevention) |
| `tests/test_git_manager.py` | **New** — 6 tests, all inside throwaway git repositories |

**Unchanged:** `tools/safety.py`, `tools/tool_registry.py`, `tools/permissions.py`, `agent/react_agent.py`, `sandbox/policy.json`, all AWS files, all workspace fixtures. The checkpoint/rollback API signature is preserved (only an optional keyword was added).

## 2. Sandbox architecture

```
agent / test code
   └─ ToolRegistry.run_python(filename)        (only registered execution tool)
        └─ tools/executor.run_python_file()    ← SINGLE CHOKEPOINT, env-controlled mode
             ├─ mode "docker" (default): docker_available() ──No──▶ FAIL CLOSED (nothing runs)
             │                              │Yes
             │                     sandbox_image_available() ──No──▶ FAIL CLOSED (nothing runs)
             │                              │Yes
             │                     run_docker(): hardened `docker run` (10s timeout)
             │                              ├─ success/failure → structured result
             │                              ├─ timeout → docker rm -f <name> → timed_out result
             │                              └─ error  → docker rm -f <name> → docker-error result
             └─ mode "local-unsandboxed": run_local() labeled UNSANDBOXED (explicit opt-in only)
```

Result structure (stable superset of the Step 1 contract): `success`, `output`, `error`, `return_code`, `execution_mode`, `timed_out`. `react_agent.reflect()`'s timeout contract (`return_code == -1` and `"timed out"` in error) is preserved and asserted by a unit test.

## 3. Docker security restrictions (actual `docker run` flags)

| Restriction | Flag |
|---|---|
| No network | `--network none` |
| CPU / memory / processes | `--cpus 1`, `--memory 256m`, `--pids-limit 64` |
| Read-only rootfs, writable tmp only | `--read-only`, `--tmpfs /tmp:rw,noexec,nosuid,size=64m` |
| Capability drop | `--cap-drop ALL` |
| No privilege escalation | `--security-opt no-new-privileges` (and no `--privileged`) |
| Non-root | `--user sandboxuser` + `USER sandboxuser` in image |
| Mounts | exactly one: `<project>/workspace:/workspace:ro` (forward slashes for Docker Desktop); never home, `.aws`, `.ssh`, Git creds, Docker socket, or the whole project |
| Ports / env | none published; no `-e`/`--env` at all → host env (AWS creds) cannot leak in |
| Cleanup | `--rm` on every path + `docker rm -f <unique-name>` on timeout/error |
| Registry | `--pull never` — a missing image never triggers a pull |
| Timeout | 10 seconds (`SANDBOX_TIMEOUT_SECONDS`), container force-removed |
| Path containment | absolute paths and `..` traversal rejected before any execution |

## 4. Execution modes and fallback behavior

`CODEPILOT_EXECUTION_MODE` (environment only — tool arguments cannot change it):

| Value | Behavior |
|---|---|
| *(unset)* / `docker` | Sandbox **required**. Docker missing → `docker-unavailable`; image missing → `image-missing`. **Nothing is executed** (`return_code -2`). **No local fallback exists.** |
| `local-unsandboxed` | Explicit development opt-in. Prints a warning banner; result labeled `execution_mode: UNSANDBOXED-local`. |
| `auto`, `local`, anything else (incl. legacy values) | `mode-rejected` — **nothing executed** (breaking change vs Step 1, intentional). |
| any filename outside `workspace/` | `path-rejected` — **nothing executed**. |

## 5. Commands executed and actual results

| Command | Actual result |
|---|---|
| `git status` (before changes) | clean, `master` ahead 1 at `f107ccb`, no stashes |
| `docker info` | daemon running, server 29.8.2 |
| `docker build -f sandbox/Dockerfile -t codepilot-sandbox .` | **exit 0**, image rebuilt (no pip layer; base `python:3.14-slim`) |
| `pytest tests\ -q` (final run) | **29 passed, 8 subtests passed in 2.56s** |
| Integration script (executor in default mode) | see §7 |
| `docker ps -a --filter name=codepilot` (after timeout test) | **no leftover containers** |
| `git add` (explicit paths) + `git commit` | `793b97b`, 7 files, +740/−98 |
| AWS / Bedrock / deploy commands | **none executed** (per restrictions) |
| Push / reset / rebase / tag deletion | **none performed** |

## 6. Unit-test results (all mocked — Docker NOT required)

**29 passed, 8 subtests passed** (pytest 9.1.1, Python 3.14.0). Coverage of the 10 required areas:

1. Docker unavailable in sandbox-required mode → `test_docker_missing_fails_closed_without_executing`
2. No local fallback → `test_daemon_failure_never_runs_local_python` (only `docker info` attempted, host python never invoked), `test_default_mode_is_docker_when_env_unset`, `test_legacy_and_unknown_modes_fail_closed`
3. Network isolation → `test_network_is_disabled` (`--network none`)
4. CPU/memory limits → `test_cpu_memory_and_pid_limits_supplied`
5. Non-root → `test_non_root_user_configured` (`--user sandboxuser` + `USER sandboxuser` in Dockerfile)
6. Timeout handling → `test_timeout_handling_kills_and_cleans_container` (timed_out, `-1`, `"timed out"` contract, force-remove called)
7. Non-zero exit codes → `test_non_zero_exit_code_reported` (rc 7, stderr propagated)
8. Cleanup after failures → same timeout test + `test_cleanup_after_run_failure` (`--rm` plus `docker rm -f` with matching container name)
9. No host-credential access → `test_single_read_only_workspace_mount_no_host_credentials` (mount set == exactly workspace:ro; no docker.sock/.aws/.ssh/privileged/ports) + `test_no_host_environment_passed` + `test_path_traversal_rejected_before_any_execution`
10. Existing compatibility → original 4 `test_agent.py` tests pass unchanged; plus `TestToolRegistryCannotBypassSandbox` (chokepoint routing, 3-tool allow-list, no mode parameter) and 6 temp-repo git tests.

## 7. Docker integration results (actually executed against the real daemon)

| # | Test | Actual output |
|---|---|---|
| R1 | `run_python_file("factorial.py")` (default mode) | `success: true, output: "120\n", return_code: 0, execution_mode: docker-sandbox` |
| R2 | `run_python_file("unknown_variable.py")` | `success: false, return_code: 1`, full `NameError` traceback captured from container |
| R3 | Isolation probe (temp file, deleted afterwards) | `UID:1000` (non-root) · `NETWORK:BLOCKED-OSError` · `/root/.aws:False` · `/home/sandboxuser/.aws:False` · `/var/run/docker.sock:False` · `HOME:/home/sandboxuser` (container home, not host) |
| R4 | Timeout probe (`run_docker(..., timeout=5)`, temp file, deleted) | `success: false, return_code: -1, timed_out: true, error: "Docker execution timed out after 5 seconds."`; `docker ps -a` afterwards: **no leftover containers** |
| R5 | Legacy mode `auto` (live) | `mode-rejected`, `return_code: -2`, "No code was executed." |
| R6 | `local-unsandboxed` (live) | `success: true, execution_mode: UNSANDBOXED-local`, warning banner printed |

**Honest limitation:** the *Docker-unavailable* fail-closed path was verified by unit tests only — the daemon was running throughout Step 2, so no live run exercised it. All other security properties above were verified live.

## 8. Remaining security limitations (unverified / by design)

1. **Docker-unavailable fail-closed**: unit-tested only (see §7). Live re-test recommended after stopping Docker Desktop (see Step 3 instructions).
2. **Rollback still uses `git reset --hard`** in the agent runtime — preserved per instructions; tests only ever run it in temp repositories. A workspace-scoped, non-destructive rollback design is deferred to Step 3 (documented here, **not** migrated now).
3. **Mode selection is an operator trust boundary**: any process able to set `CODEPILOT_EXECUTION_MODE=local-unsandboxed` can enable UNSANDBOXED execution. The tool registry/agent cannot do so (no mode parameter exists — tested).
4. **Default Docker seccomp/AppArmor profiles** apply (not custom-tuned); no custom profile was configured.
5. **`--user sandboxuser`** assumes the image was built from this Dockerfile (username exists in the image — verified live by R1–R4).
6. **Read-only workspace mount**: sandboxed code cannot write files; agent edits happen host-side via `tools/filesystem.py`. If Step 3 needs in-container writes, add a dedicated scratch dir, not a writable workspace mount.
7. **Windows-only validation**: verified with Docker Desktop (Linux containers) on Windows 11. Windows containers are unsupported/not tested.
8. **`reflect()` treats infrastructure errors (`return_code -2`) as code errors** → the agent would STOP+rollback instead of a distinct "infra unavailable" stop. Safe, but a Step 3 improvement.
9. Image base `python:3.14-slim` comes from Docker Hub — supply-chain trust rests on official images; `--pull never` prevents runtime pulls.

## 9. Exact instructions to continue to Step 3

1. Verify baseline: `git log --oneline -n 3` (expect `793b97b` + this report's commit) and `venv\Scripts\python.exe -m pytest tests\ -q` → 29 passed.
2. **Live fail-closed check (manual, ~1 min):** stop Docker Desktop, run `venv\Scripts\python.exe -c "import json; from tools.executor import run_python_file; print(json.dumps(run_python_file('factorial.py')))"` → must return `docker-unavailable` with no execution; restart Docker afterwards.
3. **Build the benchmark harness** (`benchmarks/harness.py`): drive `ReActAgent` over BENCH-001..004 in docker mode — BENCH-002 against `workspace/repair_loop_temp.py` (still buggy), BENCH-003 `unknown_variable.py` (expect STOP+rollback, then clean `git status`), BENCH-004 a repeated-failure fixture.
4. **Teach `reflect()` to distinguish infra errors** (`return_code -2`, `execution_mode` in `docker-unavailable`/`image-missing`/`mode-rejected`) from code errors — stop without treating them as code failures.
5. **Design (then implement) a safer rollback**: e.g. `git restore`/`git stash` scoped to the workspace or a checkpoint worktree, replacing `reset --hard` — design note first, no destructive migration without approval.
6. Expand unit tests: `SafetyManager` limits, `reflect()` decision paths, harness scenarios.
7. AWS work remains gated: configure credentials explicitly before any Bedrock/S3/DynamoDB step; keep `aws/deploy.py` in dry-run; never `--apply` without approval.



