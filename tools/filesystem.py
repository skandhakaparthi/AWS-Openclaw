from pathlib import Path


# Resolve workspace relative to the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = (PROJECT_ROOT / "workspace").resolve()


def _safe_path(filename):
    """Resolve a path and ensure it stays inside the workspace."""
    candidate = (WORKSPACE / filename).resolve()

    try:
        candidate.relative_to(WORKSPACE)
    except ValueError:
        raise ValueError(
            "Access denied: path must remain inside the workspace."
        )

    return candidate


def list_files():
    """List all files inside the workspace."""
    if not WORKSPACE.exists():
        return []

    return [
        str(file.relative_to(WORKSPACE))
        for file in WORKSPACE.rglob("*")
        if file.is_file()
    ]


def read_file(filename):
    """Read a file inside the workspace."""
    try:
        path = _safe_path(filename)
    except (ValueError, OSError, RuntimeError):
        return "ERROR: Access denied."

    if not path.is_file():
        return f"ERROR: File '{filename}' does not exist."

    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return f"ERROR: Could not read file '{filename}'."


def write_file(filename, content):
    """Create or modify a file inside the workspace."""
    try:
        path = _safe_path(filename)
    except (ValueError, OSError, RuntimeError):
        return "ERROR: Access denied."

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"File '{filename}' written successfully."
    except OSError:
        return f"ERROR: Could not write file '{filename}'."
