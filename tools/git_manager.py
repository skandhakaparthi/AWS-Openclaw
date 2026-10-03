import subprocess
from datetime import datetime


def run_git_command(command):
    """Run a Git command and return its output."""
    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())

    return result.stdout.strip()


def create_checkpoint():
    """
    Create a Git checkpoint before the agent modifies the project.
    """

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    checkpoint_name = f"agent-checkpoint-{timestamp}"

    # Check whether there are changes
    status = run_git_command(["git", "status", "--porcelain"])

    if status:
        # Save current changes
        run_git_command(["git", "add", "."])
        run_git_command([
            "git",
            "commit",
            "-m",
            f"Agent checkpoint {timestamp}"
        ])

    # Create a tag pointing to the current state
    run_git_command([
        "git",
        "tag",
        checkpoint_name
    ])

    print(f"✅ Checkpoint created: {checkpoint_name}")

    return checkpoint_name


def rollback(checkpoint_name):
    """
    Roll the project back to a previous checkpoint.
    """

    print(f"🔄 Rolling back to: {checkpoint_name}")

    run_git_command([
        "git",
        "reset",
        "--hard",
        checkpoint_name
    ])

    print("✅ Rollback completed.")


def get_current_commit():
    """Return the current Git commit hash."""

    return run_git_command([
        "git",
        "rev-parse",
        "HEAD"
    ])


if __name__ == "__main__":
    print("Git Manager Test")
    print("-" * 40)

    commit = get_current_commit()

    print(f"Current commit: {commit}")