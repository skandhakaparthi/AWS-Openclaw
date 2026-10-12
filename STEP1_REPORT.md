# STEP1_REPORT.md — Inspection and Preparation Report

Project: CodePilot-AI (AWS-Openclaw) — OpenClaw-style Autonomous Coding Agent
Date: 2026-10-12
Repository: `c:\Users\skand\CodePilot-AI` (branch `master`, remote `origin` → https://github.com/skandhakaparthi/AWS-Openclaw.git)

---

## 1. Project Structure

All files below were verified to exist. Nothing was created, deleted, or overwritten except this report.

```
CodePilot-AI/
├── README.md                  # Project overview, safety limits, run instructions
├── requirements.txt           # Exactly 2 deps: boto3, pytest
├── .gitignore                 # venv/, __pycache__/, *.pyc, .env, task_state/
├── agent/
│   ├── __init__.py
│   ├── react_agent.py         # MAIN entry point: ReAct loop (PLAN→ACT→OBSERVE→REFLECT),
│   │                          #   max 5 iterations, 60s runtime, $0.05 cost, runaway-loop
│   │                          #   detection, Git checkpoint + rollback, task-state persistence.
│   │                          #   `python -m agent.react_agent` runs a demo task on repair_test.py
│   ├── agent.py               # Older interactive demo (`CodingAgent`) with input(); hardcoded
│   │                          #   division-by-zero fix; requires terminal input, not headless
│   ├── planner.py             # TaskPlanner: LLM-based task decomposition (delegates to llm.client)
│   └── state.py               # TaskState: per-task JSON persistence under task_state/ (gitignored)
├── llm/
│   └── client.py              # LLMClient: "mock" (default) and "bedrock" (boto3 Converse API,
│                              #   default model amazon.nova-micro-v1:0, region ap-southeast-2).
│                              #   NOTE: no llm/__init__.py — imports work via PEP 420 namespace pkg.
├── tools/
│   ├── filesystem.py          # Workspace-confined read/write with path-traversal guard
│   ├── executor.py            # Docker sandbox execution (network=none, 1 CPU, 256m,
│   │                          #   no-new-privileges, read-only mount) + labeled local fallback
│   ├── safety.py              # SafetyManager: step/cost/runtime/repeated-error limits
│   ├── git_manager.py         # checkpoint = git tag "agent-checkpoint-<ts>" (clean tree
│   │                          #   required); rollback = git reset --hard <tag>
│   ├── tool_registry.py       # Permission-checked read_file / write_file / run_python
│   ├── permissions.py         # ToolPermissionManager: allow-list of 3 tools
│   ├── cloudwatch_logger.py       # AWS: CloudWatch Logs (not yet wired into loop)
│   ├── dynamodb_state_manager.py  # AWS: DynamoDB task state (not yet wired into loop)
│   └── s3_checkpoint_manager.py   # AWS: S3 checkpoints (not yet wired into loop)
├── sandbox/
│   ├── Dockerfile             # python:3.14-slim, non-root user, copies requirements + workspace
│   └── policy.json            # Declared sandbox policy (network off, RO rootfs, 10s timeout)
├── benchmarks/
│   ├── benchmark_suite.json   # 4 scenarios: BENCH-001..004 with expected outcomes
│   └── runner.py              # `python benchmarks\runner.py` — DISPLAY ONLY: prints the
│                              #   suite definition; does NOT execute scenarios
├── tests/
│   ├── __init__.py
│   └── test_agent.py          # 4 unittest tests, all targeting tools/filesystem.py only
├── aws/
│   ├── aws_services_config.json  # DynamoDB CodePilotTaskState, S3 codepilot-checkpoints,
│   │                             #   CW /codepilot/agent, X-Ray, IAM least-privilege map
│   ├── deploy.py                 # S3/DynamoDB/CloudWatch provisioner — DRY RUN default;
│   │                             #   `--apply` creates paid resources (NOT executed)
│   ├── deploy_lambdas.py         # Creates/updates 4 Lambdas (NOT executed — paid)
│   ├── lambda_package/handlers.py # Stub handlers; rollback is an explicit stub
│   ├── lambda_package.zip        # Committed build artifact (599 B)
│   ├── step_functions_state_machine.json # Full workflow; "Execute" is a Pass-state stub
│   │                             #   ("ECS executor integration is pending")
│   ├── step_functions_policy.json # Lambda invoke policy (hardcoded account ID)
│   ├── ecs-task-definition.json   # Fargate task def (hardcoded account ID/ECR; stub command)
│   └── architecture.mmd           # Mermaid diagram: SF→Bedrock→Fargate→Docker→EFS flow
├── workspace/                 # Agent's confined working area (all files tracked)
│   ├── buggy.py               # ALREADY CONTAINS the fix (b==0 guard) — not currently buggy
│   ├── repair_test.py         # Same fixed content; target of the default agent demo
│   ├── repair_loop_temp.py    # Still contains the division-by-zero bug
│   ├── unknown_variable.py    # NameError fixture (BENCH-003)
│   ├── calculator.py, factorial.py  # Correct fixtures
├── task_state/                # ~90 generated per-run JSON state files (gitignored, on disk)
└── venv/                      # Existing virtual environment, Python 3.14.0 (gitignored)
```

### Git / env configuration checks
- Git: pre-initialized repository with full history (20+ commits, prior `agent-checkpoint-*` tags from earlier agent runs). Identity is a placeholder: `user.name = "Your Name"`, `user.email = you@example.com`.
- Virtual environment: exists at `venv/` (not `.venv`). Left as-is; no new environment created.
- AWS configuration: `~/.aws/config` exists with only a `[default]` section; `~/.aws/credentials` does **not** exist. No AWS/Bedrock/API-key environment variables are set. No `.env` file exists anywhere in the project.

---

## 2. Environment and Dependency Status

| Item | Status |
|---|---|
| OS | Windows 11 (`win32`), PowerShell 7 |
| System Python | 3.14.0 (`C:\Python314`); Python 3.13 also installed |
| venv Python | 3.14.0 at `venv\Scripts\python.exe` — functional |
| Git | 2.50.1.windows.1 |
| AWS CLI | 2.37.12 (installed; no credentials configured) |
| Docker | Client 29.8.2 installed — **daemon NOT running** (`docker info` fails: pipe not found) |
| requirements.txt | `boto3`, `pytest` |
| Installed in venv | boto3 1.43.108, pytest 9.1.1 (+ transitive deps) — **requirements already satisfied** |
| Dependency action taken | None. No install/upgrade performed (nothing missing; no unnecessary upgrades). |
| Secrets exposure | None. Only environment variable *names* were checked; no secret values printed. |

Conclusion: the existing `venv/` fully satisfies `requirements.txt`. No `.venv` creation was necessary.

---

## 3. Git Checkpoint Status

- **State before any action:** branch `master`, in sync with `origin/master`, HEAD = `12c3bc8` ("Push project code"), working tree **clean**, no stashes, no uncommitted user changes.
- **Secret scan:** all 42 tracked files scanned for AWS key IDs, secret-key markers, API-key/password assignments, and private-key blocks → **no matches**. No `.env` files. No credentials files inside the repo.
- **.gitignore verified:** excludes `venv/`, `__pycache__/`, `*.pyc`, `.env`, `task_state/`. `.pytest_cache/` self-ignores via pytest's own generated `.gitignore`. No generated or secret file is staged.
- **History preserved:** no `git init` (already a repo), no reset, no force operations, no history rewrite. Existing commits and `agent-checkpoint-*` tags untouched.
- **No initial commit was needed** — history already existed and the tree was clean, so creating one would have been unnecessary.
- **Checkpoint established this step:** the agent's own run created tag `agent-checkpoint-20261012_144302` on `12c3bc8` (additive, safe). This report was then committed as a **new commit on top of existing history** (no history rewrite; only the new `STEP1_REPORT.md` staged).
- **Post-run verification:** `git status --porcelain` is clean again after the agent run (task-state output lands in gitignored `task_state/`).

---

## 4. Tests Executed and Actual Results

Test framework: **pytest** (declared in `requirements.txt`); tests are written with the stdlib `unittest` API, so both runners work.

| Command | Result |
|---|---|
| `venv\Scripts\python.exe -m pytest tests\ -v --tb=short` | **4 passed in 0.20s** (test_missing_file, test_read_buggy_program, test_read_calculator, test_workspace_files_exist) |
| `venv\Scripts\python.exe -m unittest discover -s tests -v` | **4 tests, OK** (0.007s) |
| `venv\Scripts\python.exe benchmarks\runner.py` | **Ran successfully** — prints suite name, safety requirements, and the 4 benchmark task definitions |

Actual results recorded verbatim; no test was modified to pass.

**Important limitations (honest assessment):**
- All 4 tests exercise only `tools/filesystem.py`. There is **no test coverage** for the ReAct loop, `SafetyManager`, `executor`, `git_manager`, or the LLM client.
- `benchmarks/runner.py` is a **printer, not an executor** — it does not run BENCH-001..004 against the agent. Running it proves only that the JSON loads and formatting works. No automated benchmark harness exists yet.

---

## 5. Agent Execution Status

**Verified entry point:** `python -m agent.react_agent` (per README; must be run from the project root because `benchmarks/...` and `task_state/...` paths are CWD-relative). `python -m agent.agent` is an interactive demo that blocks on `input()` and cannot run headless.

**Actual run performed:** `venv\Scripts\python.exe -m agent.react_agent`

- Result: **SUCCESS** — full loop executed end-to-end in ~1 second:
  - Git checkpoint created: `agent-checkpoint-20261012_144302` (clean tree required — was clean).
  - LLM mode: `mock` (default; no AWS credentials needed).
  - Iteration 1: ACT → OBSERVE (execution successful, output `Cannot divide by zero`, `execution_mode: local-fallback`) → REFLECT → `SUCCESS`.
  - Safety limits reported: 5 max iterations, 60s, $0.05 cost, repeated-error limit 2.
  - Task state saved to `task_state\task-13ddd7e4.json` with status `SUCCESS`.
  - Working tree remained clean afterward.

**Caveats discovered during verification:**
1. Execution used the **local fallback, not the Docker sandbox**, because the Docker daemon is not running (the code printed its warning as designed).
2. `workspace/repair_test.py` **already contains the fix**, so the demo reached SUCCESS immediately — the FIX path, error-analysis path, and rollback path were **not exercised by this run**. The remaining buggy fixture is `workspace/repair_loop_temp.py`.
3. Bedrock mode was not executed (no AWS credentials; per safety rules no paid AWS calls were made).

---

## 6. Security Issues Discovered

1. **Local fallback execution is not a real sandbox** — with Docker down, workspace code runs directly on the host (`tools/executor.py` labels this clearly, but it weakens isolation). Docker Desktop is installed but not running.
2. **Sandbox image not built** — `executor.py` expects an image named `codepilot-sandbox`, and no build step exists in the code; even with Docker running, `run_docker()` would fail until the image is built with the correct context.
3. **Hardcoded AWS account ID** `399527676637` in `aws/step_functions_policy.json`, `aws/step_functions_state_machine.json`, and `aws/ecs-task-definition.json` — should be parameterized before wider sharing.
4. **Placeholder Git identity** (`Your Name` / `you@example.com`) will be stamped on all new commits.
5. **`git reset --hard` rollback** (`tools/git_manager.py`) is destructive by design; safe today because checkpoint creation refuses dirty trees, but it would destroy uncommitted work if invoked otherwise.
6. **~90 generated `task_state/*.json` files** accumulate on disk unbounded (gitignored, so not a repo problem, but should be rotated).
7. **Committed binary artifact** `aws/lambda_package.zip` — build output tracked in Git; consider ignoring and rebuilding instead.
8. **LLM-generated fixes are only syntax-checked** (`ast.parse`) before being written to disk — no semantic validation yet.
9. Positive findings: **no secrets, credentials, `.env` files, or private keys** in tracked files or the working tree; `.gitignore` correctly excludes `venv/` and `.env`; workspace file tools enforce a path-traversal guard; Docker flags are hardened (network none, no-new-privileges, read-only mount, resource limits).

---

## 7. Missing Prerequisites

| Prerequisite | Status | Needed for |
|---|---|---|
| Docker daemon running | ❌ Not running (client only) | True sandboxed execution |
| `codepilot-sandbox` image built | ❌ Not built; no build step in code | `run_docker()` |
| AWS credentials | ❌ `~/.aws/credentials` missing; no env vars | `CODEPILOT_MODE=bedrock`, deploy scripts, S3/DynamoDB/CloudWatch tools |
| Automated benchmark harness | ❌ `runner.py` only prints definitions | Executing BENCH-001..004 |
| Buggy fixture for BENCH-002 | ⚠️ `buggy.py`/`repair_test.py` already fixed (`repair_loop_temp.py` still buggy) | Reproducing FIX_AND_SUCCESS |
| Step Functions ECS executor | ⚠️ Stub Pass state ("ECS executor integration is pending") | Real AWS orchestration |
| Rollback Lambda | ⚠️ Explicit stub ("restoration is pending") | AWS-side rollback |
| EFS / X-Ray wiring | ❌ Declared in config/diagram only; no code | Full AWS architecture |
| `llm/__init__.py` | ❌ Missing (works via namespace package, but fragile) | Robust package imports |

---

## 8. Exact Next Steps for Step 2

1. **Enable the real sandbox:** start Docker Desktop, then build the image from the repo root with the correct build context (e.g. `docker build -f sandbox/Dockerfile -t codepilot-sandbox .` — verify `sandbox/Dockerfile`'s `COPY` paths against the chosen context first). Re-run `python -m agent.react_agent` with `CODEPILOT_EXECUTION_MODE=docker` and confirm `execution_mode: docker-sandbox` in the OBSERVE output.
2. **Build a benchmark harness** (e.g. `benchmarks/harness.py`) that drives `ReActAgent` over BENCH-001..004 and asserts expected outcomes: BENCH-001 → `factorial.py`/`calculator.py`; BENCH-002 → target `workspace/repair_loop_temp.py` (still buggy); BENCH-003 → `unknown_variable.py` (expect STOP + rollback, verify clean `git status` afterward); BENCH-004 → a fixture producing repeated identical errors (expect `SafetyManager` RuntimeError + rollback).
3. **Expand the test suite:** add unit tests for `SafetyManager` (step/cost/runtime/runaway limits), `executor` mode selection, `git_manager` checkpoint/rollback, and the `reflect()` decision paths — none are covered today.
4. **Configure AWS access (manual, approved):** run `aws configure` or SSO login so `~/.aws/credentials` exists; then test `CODEPILOT_MODE=bedrock` with the default model (`amazon.nova-micro-v1:0`, region `ap-southeast-2`). Keep `aws/deploy.py` in dry-run mode; **do not pass `--apply` without explicit approval** (it creates billable resources).
5. **Harden for Step 2+ development:** replace the placeholder Git identity (manual user action), parameterize the account ID in the three AWS JSON files, and consider gitignoring `aws/lambda_package.zip` and rotating `task_state/`.
6. **Wire persistence tools:** integrate `DynamoDBStateManager` / `S3CheckpointManager` / `CloudWatchLogger` into the ReAct loop (currently unused) behind env-var flags so local runs stay offline.
7. **Defer Step 3+ items:** Step Functions ECS executor wiring and EFS/X-Ray remain out of scope until steps 1–6 pass locally.

---

### Verification summary (what was actually run)

| Action | Result |
|---|---|
| `git status` / `git log` / secret scan / `.gitignore` check | Clean tree, history intact, no secrets found |
| `pytest tests\ -v` | **4 passed** |
| `unittest discover -s tests` | **4 OK** |
| `python benchmarks\runner.py` | Ran (display-only by design) |
| `python -m agent.react_agent` | **SUCCESS** in 1 iteration (mock mode, local-fallback) |
| AWS deploy / Terraform / CDK / paid resources | **Not executed** (as required) |



