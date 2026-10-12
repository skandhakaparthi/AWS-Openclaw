# STEP 5 — Safe Amazon Bedrock Integration

Date: 2026-10-12  
Branch: `master`  

## Initial repository state

- The actual starting worktree was clean at `e9dc73f6a23fb8170f0c053669095314c11b98b4`, six commits ahead of `origin/master`.
- This differs from the supplied Step 5 snapshot, which listed `f456064` and uncommitted Step 4 changes. In the actual repository, `e9dc73f` is the Step 4 commit and its changes were already committed before this turn. The existing history was preserved; no reset, clean, rebase, commit, or push was performed for Step 5.
- Initial inspection found an existing prototype Bedrock path using Converse, but with no explicit timeout/retry policy, weak response parsing, no provider error taxonomy or token telemetry, and no cost opt-in gate. Prompts and task details were also printed in agent output/state.
- The executor still exposed an environment-selected `local-unsandboxed` path. Step 5 requires all agent code execution to use the hardened sandbox, so that path was removed and the executor now rejects all non-Docker modes.

## Bedrock client

- `LLMClient` remains mock-first. `CODEPILOT_MODE=bedrock` is explicit; an unavailable provider raises a typed error and never falls back to mock.
- The client uses `bedrock-runtime` `Converse`, with the existing default model ID `amazon.nova-micro-v1:0`. The request uses the documented messages, system, and `inferenceConfig` fields. AWS documents Nova Micro support through Converse and lists in-region support in `ap-southeast-2`; this establishes public model/API support, not access in this AWS account. [Nova Micro model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-micro.html) [Nova Converse guide](https://docs.aws.amazon.com/nova/latest/userguide/using-converse-api.html)
- Region, model ID, optional `BEDROCK_AWS_PROFILE`, connect/read timeouts, response token cap, prompt character cap, and response character cap use validated settings. Defaults are region `ap-southeast-2`, model `amazon.nova-micro-v1:0`, connect timeout 3 seconds, read timeout 15 seconds, max response 2,048 tokens, max prompt 48,000 characters, and max response 100,000 characters.
- Authentication uses the standard boto3 credential chain; `BEDROCK_AWS_PROFILE` selects a profile explicitly. No static credentials are accepted from this adapter's configuration.
- Botocore standard retry policy is bounded to two total attempts. Authentication, throttling, transient transport/service errors, unavailable model/request, malformed model response, and configuration errors use separate sanitized exception types. Provider messages, credentials, prompts, and authorization headers are not written by application logging.
- Plan and analysis JSON responses are parsed strictly and checked against schemas and size limits. Model content is treated as untrusted data. Model responses cannot call tools; registered file tools still enforce permissions and workspace roots, and code execution is Docker-only.
- Bedrock-mode console output omits task text, model analysis text, execution output/errors, state details, and detailed event history. Task state stores a hash and character count instead of the original request.
- Boto3 Converse returns token usage fields `inputTokens` and `outputTokens`; the adapter records those when present, adapter wall-clock latency, provider latency when returned, model ID, request count, and provider error categories. [Converse response schema](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html)

## Cost and time limits

- No pricing table was embedded and no price was assumed. Estimated cost and actual charges remain `null`; `cost_limit_enforced` remains `false`.
- Because this client cannot safely enforce the existing `$0.05` budget from known-current pricing, Bedrock mode refuses to initialize unless `CODEPILOT_ALLOW_UNMETERED_BEDROCK=true` is explicitly set. This opt-in accepts unmetered calls; it does not enable a cost cap.
- Per-request network timeouts and retries are bounded. The agent checks its 60-second runtime budget between blocking calls; a synchronous SDK call cannot be interrupted mid-request by the agent's runtime check, so an in-flight request may extend task wall time. This is a remaining lifecycle limitation.

## Tests and Docker verification

Full test command:

```powershell
.\venv\Scripts\python.exe -m unittest discover -v
```

Result: **69 tests discovered: 68 passed, 1 skipped, 0 failed**. The skipped test is the existing symlink escape test, because the Windows host did not permit symlink creation. New mocked Bedrock tests cover mock determinism/no AWS initialization, model selection, profile selection, missing credentials, auth/throttle/transient/model-access errors, bounded retries, malformed responses, prompt/token limits, token/latency telemetry, unknown cost, and no prompt logging. A ReAct regression verifies provider failures stop without generating or writing a code repair.

Docker benchmarks were run with:

```powershell
.\venv\Scripts\python.exe -m benchmarks.runner --output benchmarks/step5_results.json
```

Result: **4 passed, 0 failed, 0 blocked, 0 timed out**. Each scenario used `docker-sandbox`. The `local-unsandboxed` executor mode was separately tested to return `mode-rejected` without launching a process. After the benchmark run, no `codepilot-*` containers remained.

No AWS API call, Bedrock smoke test, deployment, profile inspection, or credential setup was performed.

## Manual Bedrock smoke test (not run)

First, configure an AWS profile yourself using your normal approved process, confirm it can invoke Bedrock, and confirm that the account has access to Nova Micro in `ap-southeast-2`. Converse requires `bedrock:InvokeModel`. Model access/agreements and account quotas may also apply. The public model list cannot confirm those account-specific settings. [Converse permissions and API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html) [Regional model support](https://docs.aws.amazon.com/bedrock/latest/userguide/models-region-compatibility.html)

In a temporary PowerShell session, set only non-secret settings and run one small, potentially billable request:

```powershell
$env:CODEPILOT_MODE = "bedrock"
$env:CODEPILOT_ALLOW_UNMETERED_BEDROCK = "true"
$env:BEDROCK_AWS_PROFILE = "your-profile-name"
$env:AWS_REGION = "ap-southeast-2"
$env:BEDROCK_MODEL_ID = "amazon.nova-micro-v1:0"
$env:BEDROCK_MAX_TOKENS = "64"
$env:BEDROCK_CONNECT_TIMEOUT_SECONDS = "3"
$env:BEDROCK_READ_TIMEOUT_SECONDS = "15"
.\venv\Scripts\python.exe -c "from llm.client import LLMClient; c=LLMClient(); print(c.generate_plan('Return one short step for inspecting a hello-world program.')); print(c.usage_summary())"
```

Afterward, remove those session variables with `Remove-Item Env:CODEPILOT_MODE,Env:CODEPILOT_ALLOW_UNMETERED_BEDROCK,Env:BEDROCK_AWS_PROFILE,Env:AWS_REGION,Env:BEDROCK_MODEL_ID,Env:BEDROCK_MAX_TOKENS,Env:BEDROCK_CONNECT_TIMEOUT_SECONDS,Env:BEDROCK_READ_TIMEOUT_SECONDS`. Do not paste credentials into chat or store them in the repository.

## Remaining gaps and next steps

- No live request verifies this AWS account's credentials, permissions, model access, service quota, or output quality.
- Cost remains unmetered when the explicit opt-in is used; no claim of budget enforcement is made.
- Synchronous provider calls cannot be cancelled at the agent's overall task deadline; only per-request SDK timeouts/retries bound them.
- Step 6 should add an optional live smoke test that is never part of ordinary CI, task-deadline cancellation or deadline-aware request budgeting, and a supported current pricing source before any `$0.05` cost enforcement is claimed. Preserve Docker-only execution and explicit review/promotion behavior.

## Git state after Step 5

HEAD remains `e9dc73f6a23fb8170f0c053669095314c11b98b4`; Step 5 files are uncommitted for review. No changes were pushed.
