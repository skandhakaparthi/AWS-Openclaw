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

The project uses a mock LLM by default, so AWS Bedrock is optional during local development. Code execution is fail-closed: by default (`CODEPILOT_EXECUTION_MODE=docker`) files run inside the Docker sandbox and are NOT executed if Docker or the sandbox image is unavailable. Local execution requires the explicit opt-in `CODEPILOT_EXECUTION_MODE=local-unsandboxed` and is clearly labeled UNSANDBOXED. Build the sandbox image once from the repository root: `docker build -f sandbox/Dockerfile -t codepilot-sandbox .`
