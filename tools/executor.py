import os
import shutil
import subprocess


def docker_available():
    """Check whether Docker is installed and accessible."""
    docker = shutil.which("docker")

    if docker is None:
        return False

    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=5
        )

        return result.returncode == 0

    except Exception:
        return False


def run_local(filename):
    """Local fallback execution.

    This is NOT a true security sandbox.
    It is only used when Docker is unavailable.
    """

    print(
        "WARNING: Docker sandbox unavailable. "
        "Using restricted local execution."
    )

    command = [
        "python",
        f"workspace/{filename}"
    ]

    try:

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10
        )

        return {
            "success": result.returncode == 0,
            "output": result.stdout,
            "error": result.stderr,
            "return_code": result.returncode,
            "execution_mode": "local-fallback"
        }

    except subprocess.TimeoutExpired:

        return {
            "success": False,
            "output": "",
            "error": "Execution timed out after 10 seconds.",
            "return_code": -1,
            "execution_mode": "local-fallback"
        }


def run_docker(filename):
    """Execute code inside Docker sandbox."""

    command = [
        "docker",
        "run",
        "--rm",

        # Disable network access
        "--network", "none",

        # Resource limits
        "--cpus", "1",
        "--memory", "256m",
        "--pids-limit", "64",

        # Prevent privilege escalation
        "--security-opt", "no-new-privileges",

        # Mount workspace read-only
        "-v",
        f"{os.path.abspath('workspace')}:/workspace:ro",

        "codepilot-sandbox",

        "python",
        f"/workspace/{filename}"
    ]

    try:

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10
        )

        return {
            "success": result.returncode == 0,
            "output": result.stdout,
            "error": result.stderr,
            "return_code": result.returncode,
            "execution_mode": "docker-sandbox"
        }

    except subprocess.TimeoutExpired:

        return {
            "success": False,
            "output": "",
            "error": "Docker execution timed out after 10 seconds.",
            "return_code": -1,
            "execution_mode": "docker-sandbox"
        }


def run_python_file(filename):
    """
    Execute a Python file using the safest available mode.

    Docker is preferred.
    Local execution is used only as a development fallback.
    """

    requested_mode = os.getenv(
        "CODEPILOT_EXECUTION_MODE",
        "auto"
    ).lower()

    if requested_mode == "docker":

        if not docker_available():
            return {
                "success": False,
                "output": "",
                "error": (
                    "Docker execution was requested, "
                    "but Docker is unavailable."
                ),
                "return_code": -2,
                "execution_mode": "docker-unavailable"
            }

        return run_docker(filename)

    if requested_mode == "local":

        return run_local(filename)

    # Automatic mode
    if docker_available():

        return run_docker(filename)

    return run_local(filename)


if __name__ == "__main__":

    print("CODEPILOT EXECUTOR")
    print("-" * 50)

    if docker_available():
        print("Docker: AVAILABLE")
        print("Default mode: Docker sandbox")
    else:
        print("Docker: NOT AVAILABLE")
        print("Default mode: Local fallback")
        print("WARNING: Local fallback is NOT a true sandbox.")
