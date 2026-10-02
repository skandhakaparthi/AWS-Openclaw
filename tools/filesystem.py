from pathlib import Path


# The agent is only allowed to work inside this folder
WORKSPACE = Path("workspace")


def list_files():
    """List all files inside the workspace."""

    files = []

    for file in WORKSPACE.rglob("*"):
        if file.is_file():
            files.append(str(file))

    return files


def read_file(filename):
    """Read a file from the workspace."""

    path = WORKSPACE / filename

    if not path.exists():
        return f"ERROR: File '{filename}' does not exist."

    return path.read_text(encoding="utf-8")


def write_file(filename, content):
    """Create or modify a file inside the workspace."""

    path = WORKSPACE / filename

    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(content, encoding="utf-8")

    return f"File '{filename}' written successfully."