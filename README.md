# CodePilot-AI

## Autonomous Coding Agent

CodePilot-AI is an OpenClaw-style autonomous coding agent that plans coding tasks, executes code, observes results, analyzes errors, applies safe corrections, and rolls back unsafe changes.

## Core Workflow

PLAN -> ACT -> OBSERVE -> REFLECT -> SUCCESS / FIX / ROLLBACK

## Implemented Features

- ReAct Plan-Act-Observe-Reflect loop
- Task decomposition
- Tool registry and permissions
- Error analysis and self-correction
- Git checkpoints and rollback
- Maximum iteration limit
- Runtime limit
- Cost limit
- Runaway-loop detection
- Persistent task state
- Docker sandbox configuration
- Benchmark suite
- AWS deployment architecture

## Safety Limits

| Control | Limit |
|---|---:|
| Maximum iterations | 5 |
| Maximum runtime | 60 seconds |
| Maximum estimated cost | .05 |
| Repeated identical errors | 2 |
| Rollback on unsafe failure | Enabled |

## AWS Architecture

Step Functions -> Bedrock -> Fargate/Docker Sandbox -> EFS Workspace -> Execute -> Observe -> Reflect

Supporting services:

- S3 - checkpoints and artifacts
- DynamoDB - task state and iteration history
- CloudWatch - logs and metrics
- X-Ray - execution tracing
- IAM - least-privilege permissions

## Benchmark Scenarios

1. Working program -> SUCCESS
2. Division by zero -> FIX_AND_SUCCESS
3. Unknown variable -> STOP_AND_ROLLBACK
4. Repeated failure -> SAFETY_STOP_AND_ROLLBACK

## Run Locally

    python -m agent.react_agent
    python benchmarks\runner.py

## Development Mode

The project uses a mock LLM by default, so AWS Bedrock is optional during local development. Code execution is Docker-only and fail-closed: by default (`CODEPILOT_EXECUTION_MODE=docker`) files run inside the hardened Docker sandbox and are NOT executed if Docker or the sandbox image is unavailable. No local Python fallback is implemented. Build the sandbox image once from the repository root: `docker build -f sandbox/Dockerfile -t codepilot-sandbox .`

Bedrock mode is opt-in with `CODEPILOT_MODE=bedrock`. Configure `AWS_REGION` (default `ap-southeast-2`), `BEDROCK_MODEL_ID` (default `amazon.nova-micro-v1:0`), and optionally `BEDROCK_AWS_PROFILE` using the standard AWS shared configuration/credential chain. Request timeout and token/prompt limits are controlled by `BEDROCK_CONNECT_TIMEOUT_SECONDS`, `BEDROCK_READ_TIMEOUT_SECONDS`, `BEDROCK_MAX_TOKENS`, `BEDROCK_MAX_PROMPT_CHARS`, and `BEDROCK_MAX_RESPONSE_CHARS`. Because the agent cannot enforce the `$0.05` task budget without current pricing, a live Bedrock client refuses to initialize unless `CODEPILOT_ALLOW_UNMETERED_BEDROCK=true` is explicitly set. This acknowledges unmetered use; it does not enforce a cost cap. No credentials belong in project files.
