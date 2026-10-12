"""Isolated task workspaces, review artifacts, and explicit promotion."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path


TASK_ID_RE = re.compile(r"\Atask-[A-Za-z0-9_-]+\Z")


def _digest(data: bytes | None):
    return None if data is None else hashlib.sha256(data).hexdigest()


def _safe_relative(root: Path, relative: str):
    if not isinstance(relative, str) or not relative:
        raise ValueError("A non-empty relative file path is required.")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("Path escapes its assigned workspace.") from exc
    return candidate


class TaskWorkspaceManager:
    """Copy a workspace to a temporary root and preserve successful work as a patch."""

    def __init__(self, primary_workspace, artifact_root, temp_root=None):
        self.primary_workspace = Path(primary_workspace).resolve()
        self.artifact_root = Path(artifact_root).resolve()
        self.temp_root = Path(temp_root or (Path(tempfile.gettempdir()) / "codepilot-task-workspaces")).resolve()
        self.task_root = None
        self.workspace = None
        self.baseline = None

    @staticmethod
    def _snapshot(root):
        snapshot = {}
        for path in root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                snapshot[relative] = "symlink:" + os.readlink(path)
            elif path.is_file():
                snapshot[relative] = _digest(path.read_bytes())
        return snapshot

    def create(self, task_id):
        if not TASK_ID_RE.fullmatch(task_id):
            raise ValueError("Invalid task ID.")
        if not self.primary_workspace.is_dir():
            raise ValueError("Primary workspace does not exist.")
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.task_root = Path(tempfile.mkdtemp(prefix=task_id + "-", dir=self.temp_root)).resolve()
        try:
            self.workspace = self.task_root / "workspace"
            shutil.copytree(self.primary_workspace, self.workspace, symlinks=True)
            self.baseline = self._snapshot(self.primary_workspace)
            return self.workspace
        except BaseException:
            self.cleanup()
            raise

    def create_review_artifact(self, task_id, modified_files, task_status, test_result=None):
        if self.workspace is None or self.baseline is None:
            raise RuntimeError("Create the task workspace before creating an artifact.")
        if not TASK_ID_RE.fullmatch(task_id):
            raise ValueError("Invalid task ID.")

        after = self._snapshot(self.workspace)
        changed = sorted(
            name for name in set(self.baseline) | set(after)
            if self.baseline.get(name) != after.get(name)
        )
        declared = set(modified_files)
        unexpected = sorted(set(changed) - declared)
        if unexpected:
            raise RuntimeError("Workspace contains undeclared changes: " + ", ".join(unexpected))

        entries = []
        patch_lines = []
        conflicts = []
        for relative in changed:
            primary_path = _safe_relative(self.primary_workspace, relative)
            task_path = _safe_relative(self.workspace, relative)
            before_bytes = primary_path.read_bytes() if primary_path.is_file() else None
            after_bytes = task_path.read_bytes() if task_path.is_file() else None
            if primary_path.is_symlink() or task_path.is_symlink():
                raise ValueError(f"Changed symlink is not eligible for promotion: {relative}")
            try:
                before_text = before_bytes.decode("utf-8") if before_bytes is not None else ""
                after_text = after_bytes.decode("utf-8") if after_bytes is not None else ""
            except UnicodeDecodeError as exc:
                raise ValueError(f"Binary change cannot be represented as a review patch: {relative}") from exc

            current_primary = primary_path.read_bytes() if primary_path.is_file() else None
            conflict = _digest(current_primary) != self.baseline.get(relative)
            if conflict:
                conflicts.append(relative)
            entry = {
                "path": relative,
                "base_sha256": self.baseline.get(relative),
                "current_primary_sha256": _digest(current_primary),
                "task_sha256": _digest(after_bytes),
                "deleted": after_bytes is None,
                "primary_conflict": conflict,
                "new_content": after_text if after_bytes is not None else None,
            }
            entries.append(entry)
            patch_lines.extend(difflib.unified_diff(
                before_text.splitlines(keepends=True),
                after_text.splitlines(keepends=True),
                fromfile=("a/" + relative) if before_bytes is not None else "/dev/null",
                tofile=("b/" + relative) if after_bytes is not None else "/dev/null",
            ))

        self.artifact_root.mkdir(parents=True, exist_ok=True)
        artifact_dir = self.artifact_root / task_id
        artifact_dir.mkdir(parents=True, exist_ok=False)
        patch_path = artifact_dir / "changes.patch"
        manifest_path = artifact_dir / "manifest.json"
        patch_path.write_text("".join(patch_lines), encoding="utf-8")
        manifest = {
            "task_id": task_id,
            "status": task_status,
            "modified_files": changed,
            "test_result": test_result,
            "primary_conflicts": conflicts,
            "promotion_requires_explicit_approval": True,
            "files": entries,
            "patch_path": str(patch_path),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return {"artifact_dir": str(artifact_dir), "patch_path": str(patch_path),
                "manifest_path": str(manifest_path), "primary_conflicts": conflicts,
                "modified_files": changed}

    def apply_reviewed_artifact(self, manifest_path, approved=False):
        if approved is not True:
            raise PermissionError("Explicit approval is required to apply a task artifact.")
        manifest_file = Path(manifest_path).resolve()
        try:
            manifest_file.relative_to(self.artifact_root)
        except ValueError as exc:
            raise ValueError("Artifact path is outside the artifact root.") from exc
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        conflicts = []
        destinations = []
        for entry in manifest.get("files", []):
            relative = entry.get("path")
            destination = _safe_relative(self.primary_workspace, relative)
            current = destination.read_bytes() if destination.is_file() else None
            if _digest(current) != entry.get("base_sha256"):
                conflicts.append(relative)
            if entry.get("deleted"):
                raise ValueError("Deletion patches require a separate reviewed workflow.")
            destinations.append((destination, entry.get("new_content")))
        if conflicts:
            return {"applied": False, "conflicts": sorted(conflicts)}
        for destination, content in destinations:
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=".codepilot-", dir=destination.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                    stream.write(content)
                os.replace(temp_name, destination)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        return {"applied": True, "files": [str(path) for path, _ in destinations]}

    def cleanup(self):
        if self.task_root is None:
            return {"success": True, "removed": False}
        root = self.temp_root.resolve()
        target = self.task_root
        if target.is_symlink():
            return {"success": False, "removed": False, "error": "Task root became a symlink."}
        try:
            resolved = target.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            return {"success": False, "removed": False, "error": f"Refusing unsafe task cleanup: {exc}"}
        try:
            shutil.rmtree(resolved)
        except OSError as exc:
            return {"success": False, "removed": False, "error": str(exc)}
        return {"success": True, "removed": True}
