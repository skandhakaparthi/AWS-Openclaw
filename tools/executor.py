import subprocess


def run_python_file(filename):
    """Run a Python file inside the workspace."""

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
            "return_code": result.returncode
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "output": "",
            "error": "Execution timed out.",
            "return_code": -1
        }