import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

# Project root / workspace, resolved from this file (never from the caller's
# CWD), so containment checks are deterministic regardless of where the agent
# is launched from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = (PROJECT_ROOT / "workspace").resolve()

# Sandbox image. Build from the repository root:
#   docker build -f sandbox/Dockerfile -t codepilot-sandbox .
SANDBOX_IMAGE = "codepilot-sandbox"
SANDBOX_TIMEOUT_SECONDS = 10

# CODEPILOT_EXECUTION_MODE values:
#   docker            -> sandbox REQUIRED (default). Fail closed: if Docker
#                        or the image is unavailable, nothing is executed.
#   local-unsandboxed -> explicit development opt-in; results are clearly
#                        labeled UNSANDBOXED. Never selected automatically.
# Any other value (including the legacy "auto" and "local" modes) is
# rejected without executing anything.
MODE_DOCKER = "docker"
MODE_LOCAL_UNSANDBOXED = "local-unsandboxed"
DEFAULT_MODE = MODE_DOCKER


def docker_available():
    """Check whether Docker is installed and the daemon is reachable."""
    docker = shutil.which("docker")

    if docker is None:
        return False

    try:
        result = subprocess.run(
            [docker, "info"],
            capture_output=True,
            text=True,
            timeout=5
        )

        return result.returncode == 0

    except Exception:
        return False


def sandbox_image_available():
    """Check that the sandbox image exists locally (never pulls)."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", SANDBOX_IMAGE],
            capture_output=True,
            text=True,
            timeout=10
        )

        return result.returncode == 0

    except Exception:
        return False


def resolve_mode():
    """Return the configured execution mode, or None when it is rejected.

    The mode is read from the environment only. Callers (including the
    agent's tool registry) cannot override it through tool arguments, so no
    registered tool can bypass the sandbox requirement.
    """
    mode = os.getenv("CODEPILOT_EXECUTION_MODE", DEFAULT_MODE)
    mode = (mode or DEFAULT_MODE).strip().lower()

    if mode in (MODE_DOCKER, MODE_LOCAL_UNSANDBOXED):
        return mode

    return None


def safe_workspace_relative(filename):
    """Return the POSIX-style path of filename inside workspace, or None.

    Rejects empty names, absolute paths and any traversal outside the
    workspace, so neither the sandbox nor UNSANDBOXED local mode can reach
    host files such as ~/.aws, ~/.ssh or the Docker socket.
    """
    if not filename or not isinstance(filename, str):
        return None

    try:
        resolved = (WORKSPACE / filename).resolve()
        relative = resolved.relative_to(WORKSPACE)
    except (ValueError, OSError, RuntimeError):
        return None

    return relative.as_posix()


def _not_executed(mode, message):
    """Fail-closed result: no code was executed."""
    return {
        "success": False,
        "output": "",
        "error": message,
        "return_code": -2,
        "execution_mode": mode,
        "timed_out": False,
    }


def remove_container(container_name):
    """Best-effort container cleanup for timeout and error paths.

    `docker run --rm` removes the container on normal exit; this covers the
    cases where the container is still running (timeout) or the run failed
    after creation, so no container is ever left behind.
    """
    try:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            text=True,
            timeout=15
        )
    except Exception:
        pass


def build_docker_command(filename, container_name):
    """Build the hardened `docker run` command for a workspace file.

    Security properties enforced here:
    - no network access (--network none)
    - CPU / memory / process limits
    - read-only root filesystem; only /tmp is a small writable tmpfs
    - all capabilities dropped, no-new-privileges
    - non-root user (redundant with USER in the image)
    - exactly one bind mount: the workspace, read-only (never the home
      directory, AWS credentials, SSH keys, Git credentials, or the
      Docker socket)
    - no published ports, no privileged mode, no host environment passed
    - --rm so the container is removed on every exit path
    - --pull never so a missing image can never be pulled from a registry
    """
    relative = safe_workspace_relative(filename)

    if relative is None:
        raise ValueError(
            f"Rejected '{filename}': path must stay inside the workspace."
        )

    # Docker Desktop on Windows accepts forward-slash host paths.
    host_workspace = str(WORKSPACE).replace("\\", "/")

    return [
        "docker", "run",
        "--rm",
        "--pull", "never",
        "--name", container_name,

        # Disable network access
        "--network", "none",

        # Resource limits
        "--cpus", "1",
        "--memory", "256m",
        "--pids-limit", "64",

        # Read-only root filesystem; small writable /tmp only
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",

        # Privilege restrictions
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "sandboxuser",

        # Single read-only mount: the workspace (forward slashes for
        # Docker Desktop on Windows)
        "-v", f"{host_workspace}:/workspace:ro",

        SANDBOX_IMAGE,

        "python",
        f"/workspace/{relative}",
    ]


def run_docker(filename, timeout=SANDBOX_TIMEOUT_SECONDS):
    """Execute code inside the Docker sandbox (fail closed, no fallback)."""

    container_name = "codepilot-" + uuid.uuid4().hex[:12]

    try:
        command = build_docker_command(filename, container_name)
    except ValueError as error:
        return _not_executed("path-rejected", str(error))

    try:

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        return {
            "success": result.returncode == 0,
            "output": result.stdout,
            "error": result.stderr,
            "return_code": result.returncode,
            "execution_mode": "docker-sandbox",
            "timed_out": False,
        }

    except subprocess.TimeoutExpired:

        # Stop and remove the still-running container.
        remove_container(container_name)

        return {
            "success": False,
            "output": "",
            "error": f"Docker execution timed out after {timeout} seconds.",
            "return_code": -1,
            "execution_mode": "docker-sandbox",
            "timed_out": True,
        }

    except Exception as error:

        # Container run failed: make sure nothing is left behind.
        remove_container(container_name)

        return {
            "success": False,
            "output": "",
            "error": f"Docker execution failed: {error}",
            "return_code": -2,
            "execution_mode": "docker-error",
            "timed_out": False,
        }


def run_local(filename, timeout=SANDBOX_TIMEOUT_SECONDS):
    """UNSANDBOXED local development execution (explicit opt-in only).

    This is NOT a security sandbox. It runs only when the operator has
    explicitly set CODEPILOT_EXECUTION_MODE=local-unsandboxed, and every
    result is labeled UNSANDBOXED.
    """
    relative = safe_workspace_relative(filename)

    if relative is None:
        return _not_executed(
            "path-rejected",
            f"Rejected '{filename}': path must stay inside the workspace.",
        )

    print("=" * 60)
    print("WARNING: UNSANDBOXED LOCAL EXECUTION (development opt-in)")
    print("This is NOT a security sandbox.")
    print("=" * 60)

    command = [sys.executable, str(WORKSPACE / relative)]

    try:

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        return {
            "success": result.returncode == 0,
            "output": result.stdout,
            "error": result.stderr,
            "return_code": result.returncode,
            "execution_mode": "UNSANDBOXED-local",
            "timed_out": False,
        }

    except subprocess.TimeoutExpired:

        return {
            "success": False,
            "output": "",
            "error": f"Execution timed out after {timeout} seconds.",
            "return_code": -1,
            "execution_mode": "UNSANDBOXED-local",
            "timed_out": True,
        }

    except Exception as error:

        return {
            "success": False,
            "output": "",
            "error": f"UNSANDBOXED local execution failed: {error}",
            "return_code": -2,
            "execution_mode": "UNSANDBOXED-local",
            "timed_out": False,
        }


def run_python_file(filename):
    """
    Execute a Python file for the coding agent.

    Fail-closed contract:
    - Default mode is "docker": the sandbox is REQUIRED. If Docker or the
      sandbox image is unavailable, nothing is executed and a clear error
      is returned. There is no local fallback.
    - Local execution only happens with the explicit opt-in
      CODEPILOT_EXECUTION_MODE=local-unsandboxed, and results are labeled
      UNSANDBOXED.
    - The mode comes from the environment only, so callers (including the
      agent's tool registry) cannot bypass the sandbox by picking another
      tool or argument.
    """
    mode = resolve_mode()

    if mode is None:
        raw = os.getenv("CODEPILOT_EXECUTION_MODE", DEFAULT_MODE)
        return _not_executed(
            "mode-rejected",
            f"Rejected CODEPILOT_EXECUTION_MODE='{raw}'. Valid modes: "
            f"'{MODE_DOCKER}' (default, sandbox required) and "
            f"'{MODE_LOCAL_UNSANDBOXED}' (explicit UNSANDBOXED opt-in). "
            "No code was executed.",
        )

    if mode == MODE_DOCKER:
        if not docker_available():
            return _not_executed(
                "docker-unavailable",
                "Sandbox-required mode: Docker is unavailable. "
                "No code was executed. Start Docker Desktop or, for "
                "development only, explicitly set "
                f"CODEPILOT_EXECUTION_MODE={MODE_LOCAL_UNSANDBOXED} "
                "(UNSANDBOXED).",
            )

        if not sandbox_image_available():
            return _not_executed(
                "image-missing",
                f"Sandbox image '{SANDBOX_IMAGE}' not found. No code was "
                "executed. Build it from the repository root with: "
                f"docker build -f sandbox/Dockerfile -t {SANDBOX_IMAGE} .",
            )

        return run_docker(filename)

    # mode == MODE_LOCAL_UNSANDBOXED (explicit opt-in only)
    return run_local(filename)


if __name__ == "__main__":
    print("CODEPILOT EXECUTOR")
    print("-" * 50)

    mode = resolve_mode()
    print(f"Configured mode: {mode or 'REJECTED'}")
    print(f"Docker available: {'YES' if docker_available() else 'NO'}")
    print(
        "Sandbox image present: "
        f"{'YES' if sandbox_image_available() else 'NO'}"
    )
    print("Default mode: docker (fail-closed, no local fallback)")
    print(
        "Local execution requires explicit opt-in: "
        "CODEPILOT_EXECUTION_MODE=local-unsandboxed"
    )
    print("Local execution is labeled: UNSANDBOXED")
