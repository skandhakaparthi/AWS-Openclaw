# CodePilot-AI sandbox image.
#
# Build from the repository root (build context must include workspace/):
#   docker build -f sandbox/Dockerfile -t codepilot-sandbox .
#
# The executor runs this image with:
#   --network none, --read-only, --cap-drop ALL, no-new-privileges,
#   --user sandboxuser, CPU/memory/pids limits, --rm, 10s timeout.
# Application code is never executed during the image build.

FROM python:3.14-slim

# Non-root execution user (also enforced via `docker run --user sandboxuser`).
RUN useradd --create-home --shell /usr/sbin/nologin sandboxuser

WORKDIR /workspace

# Benchmark/workspace scripts use only the Python standard library, so no
# third-party packages are installed (keeps the image small).
COPY workspace/ /workspace/

USER sandboxuser

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Inert default; the executor always overrides the command.
CMD ["python", "-V"]
