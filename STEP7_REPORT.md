# Step 7 Report — GitHub Actions CI

## Files changed

- `.github/workflows/ci.yml` — test, security, and Docker integration jobs.
- `requirements.txt` — exact pins for direct and resolved project dependencies.
- `STEP7_REPORT.md` — this report.

No existing project source, promotion behavior, AWS configuration, or deployment flow was changed. No commit or push was made.

## Workflow configuration

- Triggers on pushes to branches and pull requests.
- Cancels older runs for the same branch or pull request.
- Sets workflow token permissions to `contents: read`; unspecified permissions are not granted.
- Uses only `pull_request`, so fork pull requests do not receive repository secrets. The workflow passes no secrets or AWS credentials to jobs.
- Pins GitHub Actions by full commit SHA. No job invokes Bedrock, AWS, deployment, or agent patch promotion.

## Jobs

1. **Unit tests** — Ubuntu 24.04; Python 3.10 through 3.14. Installs pinned dependencies, runs `compileall`, critical Ruff checks (`E9,F63,F7,F82`), and the complete pytest suite. JUnit reports are uploaded even when tests fail.
2. **Security** — Ubuntu 24.04; audits pinned Python dependencies with `pip-audit`, then scans full Git history with the Gitleaks CLI. Gitleaks is downloaded at the pinned version and its release checksum is verified before execution. This avoids requiring a Gitleaks license, PR-comment permission, or extra token.
3. **Docker benchmarks** — Ubuntu 24.04; Python 3.14, `CODEPILOT_EXECUTION_MODE=docker`, verifies the daemon, builds `codepilot-sandbox`, and runs BENCH-001 through BENCH-004 through the real executor. It checks the scenario set, all acceptance checks, Docker execution mode, and temporary workspace cleanup, then uploads benchmark JSON. An always-run cleanup step removes any remaining `codepilot-` containers.

The benchmark job uses Linux because GitHub-hosted Ubuntu provides Docker Engine directly. The sandbox remains network-disabled and read-only as enforced by the existing executor. The runner has no local execution fallback; missing Docker or image/build failure fails the job.

## Local verification

- Workflow YAML parsed successfully with PyYAML; expected three jobs and workflow name were checked.
- `actionlint` 1.7.12 — passed with no workflow issues (release checksum verified).
- `venv\Scripts\python.exe -m compileall -q agent llm tools benchmarks tests` — passed.
- `venv\Scripts\ruff.exe check --select E9,F63,F7,F82 agent llm tools benchmarks tests` — passed.
- `venv\Scripts\pip-audit.exe --requirement requirements.txt` — **No known vulnerabilities found**.
- Gitleaks 8.30.1 — checksum verified; Git history scan passed and working-tree scan passed.
- `venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider` — **75 passed, 1 skipped, 9 subtests passed**.
- `venv\Scripts\python.exe benchmarks\runner.py --output <temporary-file>` — exit code 0; BENCH-001 through BENCH-004 passed in `docker-sandbox`, with 0 failed, blocked, or timed out.
- `docker build --file sandbox/Dockerfile --tag codepilot-sandbox .` — passed locally. The benchmark run after the build passed all scenarios, and `docker ps -a --filter name=codepilot` showed no remaining containers.
- Local Python is 3.14. The full five-version Python matrix and an actual hosted GitHub Actions run cannot be executed from this Windows checkout.

## Viewing results and limitations

After the workflow is pushed, open the repository's **Actions** tab, select **CodePilot-AI CI**, and open a run. Unit test XML and benchmark JSON are attached as workflow artifacts for 14 days. No GitHub Actions result exists yet, so this report does not claim a hosted run passed.

Dependency versions are pinned, but package artifacts are fetched from PyPI during each run rather than vendored. The security job audits project dependencies; it does not audit the runner's preinstalled operating system packages. The workflow is CI only: it does not publish artifacts as releases, deploy resources, or promote patches.

## Git state

At inspection, HEAD was `e9dc73f6a23fb8170f0c053669095314c11b98b4`, branch `master`, six commits ahead of `origin/master`; there were no staged changes and there were pre-existing Step 5/6 working-tree changes. At completion, HEAD and branch are unchanged, nothing is staged, and those existing modifications remain. Step 7 adds only this report, the workflow, and dependency pins.
