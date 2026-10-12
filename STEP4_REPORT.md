# STEP 4 — Workspace Isolation, Safe Rollback, and Task Lifecycle

Date: 2026-10-12  
Branch: `master`  

## Git preservation and provenance

- Initial Step 4 status was clean: `## master...origin/master [ahead 5]`.
- Initial HEAD was `f4560644dc67aed039ab12777bc2bb53f0988108`.
- There were no staged or unstaged changes at the start of Step 4.
- `f456064` is a direct child of `96b56ca`; its reflog entry is a normal commit entry at `2026-10-12 16:58:34 +0530`. No reset, rebase, amend, cherry-pick, or forced update was used during this Step 4 work. The commit remains in history; it was not undone.
- `origin/master` remains at `12c3bc8` with the existing checkpoint tag `agent-checkpoint-20261012_144302`. Existing tags were not changed.
- No commit or push was made for Step 4. The Step 4 changes are left uncommitted.

## Workspace and task lifecycle

- A default agent run copies `workspace/` into a unique directory under the operating system temporary directory. The tools and Docker executor receive only that copy. The project source workspace is not modified by execution or repair.
- File tools and the executor resolve paths and reject traversal or resolved paths outside their assigned root. Docker still mounts only the task workspace at `/workspace:ro`; the root filesystem is read-only, the network is disabled, the sandbox user is non-root, and no credentials, Docker socket, or host environment are passed.
- Successful default runs retain a task-specific unified patch and JSON manifest under `.agent_tasks/<task-id>/`. The manifest records changed paths, base and task SHA-256 hashes, test result, and conflicts against current primary files. Task state is retained separately under `.agent_tasks/_task_state/`. Temporary execution copies are removed after artifact creation.
- `TaskWorkspaceManager.apply_reviewed_artifact(path, approved=True)` is the explicit promotion API. It refuses application without the exact `approved=True` argument and checks all file base hashes before writing any file. Newer primary edits result in a conflict response without partial writes. Deletion patches and binary changes are rejected for promotion.
- Failed tasks use the existing file-scoped rollback, which restores only files whose current task-copy contents still match the agent's expected contents. Default task workspaces are then discarded. The repository-wide `tools.git_manager.rollback()` now refuses to run; it no longer invokes `git reset --hard`.
- Task statuses include `CREATED`, `RUNNING`, `SUCCESS`, `FAILED`, `ROLLED_BACK`, `CANCELLED`, and `CLEANUP_FAILED`. The default agent records cleanup failures and does not report overall success if its task directory could not be removed. The Docker executor reports failed container cleanup and attempts cleanup on cancellation.
- Benchmark execution retains its isolated `TemporaryDirectory` model and now places task-state files inside each run's temporary directory.

## Verification

Full test command:

```powershell
.\venv\Scripts\python.exe -m unittest discover -v
```

Result: **55 tests discovered: 54 passed, 1 skipped, 0 failed**. The skipped test is the symlink escape test because this Windows environment did not permit symlink creation. Coverage includes task-copy separation, cleanup containment, traversal and symlink rejection where available, retained patch artifacts, explicit approval, primary-edit conflict handling without partial promotion, preservation of repository-wide edits through refusal of reset-based rollback, executor cancellation/timeout cleanup, and failure reporting.

Real benchmark command:

```powershell
.\venv\Scripts\python.exe -m benchmarks.runner --output benchmarks/step4_results.json
```

Results in `benchmarks/step4_results.json`: **4 passed, 0 failed, 0 blocked, 0 timed out**. All four scenarios reported `execution_mode: docker-sandbox`.

| Scenario | Result | Evidence |
|---|---|---|
| BENCH-001 | PASSED | Docker run succeeded, exit 0, output `120`. |
| BENCH-002 | PASSED | `ZeroDivisionError` observed; repair recorded; re-execution exited 0 with `Cannot divide by zero`. |
| BENCH-003 | PASSED | `NameError` observed; agent refused repair; rollback verified; task workspace discarded. |
| BENCH-004 | PASSED | Repeated error detected; stopped at iteration 2; rollback verified; task workspace discarded. |

Docker reported version 29.8.2; daemon `docker info` succeeded; image `codepilot-sandbox` was present with ID `sha256:eb8957b6985a45ac1174d9be83550afbf44834b206814460a29767ebda0257da`. After benchmarks, `docker ps -a --filter name=codepilot-` returned no containers. The executor regression tests verify the workspace is the sole read-only mount, `--network none`, sandbox user, read-only root, no host environment, and no credential/socket mounts. Prior Step 3 real probes also recorded UID 1000, blocked network, no visible AWS credentials or Docker socket, and successful timeout cleanup.

## Remaining limitations

- The benchmark LLM is a deterministic mock; these results validate the agent/executor flow, not live model capability. No AWS credentials, Bedrock calls, deployment, or model-cost measurement were used. Cost limits are not claimed to be enforced.
- Promotion is a small Python API, not a user-facing review command or UI. Per-file replacement is atomic and conflicts are checked as a set before writing, but a host I/O failure midway through multiple replacements can still leave a partial promotion. Deletions and binary-file promotion remain unsupported.
- The symlink-escape regression could not execute on this host; it is reported as skipped rather than passed.
- An explicitly injected workspace remains caller-owned and is not cleaned by `ReActAgent`; the caller must manage its lifecycle. The benchmark runner does so with `TemporaryDirectory`.

## Step 5 instructions

Build a user-facing review/apply command around the retained manifest: show the patch and hash/conflict status, require an explicit approval input, and apply the reviewed artifact with rollback staging so a filesystem failure cannot leave a partial multi-file promotion. Add tests for that command, supported deletions if implemented, and cleanup failure reporting. Keep benchmark temporary directories caller-owned, preserve the Docker-required default, and leave model cost marked unavailable until the provider exposes measured usage.
