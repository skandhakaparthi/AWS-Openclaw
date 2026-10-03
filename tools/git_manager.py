import subprocess
from datetime import datetime


def run_git_command(command):
    result = subprocess.run(command, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())

    return result.stdout.strip()


def create_checkpoint():
    status = run_git_command(["git", "status", "--porcelain"])

    if status:
        raise RuntimeError(
            "Working tree is not clean. Commit changes before creating a checkpoint."
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    checkpoint_name = f"agent-checkpoint-{timestamp}"

    run_git_command(["git", "tag", checkpoint_name])

    print(f"Checkpoint created: {checkpoint_name}")

    return checkpoint_name


def rollback(checkpoint_name):
    print(f"Rolling back to: {checkpoint_name}")

    run_git_command(["git", "reset", "--hard", checkpoint_name])

    print("Rollback completed.")


def get_current_commit():
    return run_git_command(["git", "rev-parse", "HEAD"])


if __name__ == "__main__":
    print("Git Manager Test")
    print("-" * 40)
    print(f"Current commit: {get_current_commit()}")
