"""Isolated task workspaces, review artifacts, and explicit promotion."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import tempfile
import stat
from pathlib import Path


TASK_ID_RE = re.compile(r"\Atask-[A-Za-z0-9_-]+\Z")


def _digest(data: bytes | None):
    return None if data is None else hashlib.sha256(data).hexdigest()


def _safe_relative(root: Path, relative: str):
    if (not isinstance(relative, str) or not relative or "\\" in relative
            or relative.startswith("/") or any(part in ("", ".", "..") for part in relative.split("/"))):
        raise ValueError("A non-empty relative file path is required.")
    root = root.resolve()
    candidate = root.joinpath(*relative.split("/"))
    # Do not resolve through links: a symlink at any existing component is unsafe.
    cursor = root
    for part in relative.split("/"):
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("Symbolic links are not eligible for promotion.")
    try:
        candidate.relative_to(root)
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
        if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
            raise ValueError("Invalid promotion manifest.")
        artifact_dir = manifest_file.parent
        if manifest.get("task_id") != artifact_dir.name:
            raise ValueError("Manifest task ID does not match its artifact directory.")
        patch_file = artifact_dir / "changes.patch"
        if patch_file.is_symlink() or not patch_file.is_file():
            raise ValueError("Review patch is missing or unsafe.")
        if manifest.get("patch_path") != str(patch_file):
            raise ValueError("Review patch path does not match the artifact directory.")
        if not isinstance(manifest.get("modified_files"), list):
            raise ValueError("Invalid modified file list.")
        conflicts = []
        destinations = []
        seen = set()
        for entry in manifest.get("files", []):
            if not isinstance(entry, dict):
                raise ValueError("Invalid promotion file entry.")
            relative = entry.get("path")
            destination = _safe_relative(self.primary_workspace, relative)
            if relative in seen:
                raise ValueError("Duplicate path in promotion manifest.")
            seen.add(relative)
            if destination.exists() and not destination.is_file():
                raise ValueError(f"Destination is not a regular file: {relative}")
            current = destination.read_bytes() if destination.is_file() else None
            if _digest(current) != entry.get("base_sha256"):
                conflicts.append(relative)
            if entry.get("deleted"):
                raise ValueError("Deletion patches require a separate reviewed workflow.")
            content = entry.get("new_content")
            if not isinstance(content, str) or _digest(content.encode("utf-8")) != entry.get("task_sha256"):
                raise ValueError(f"Invalid content digest for {relative}.")
            destinations.append((relative, destination, content, current,
                                 destination.stat() if destination.exists() else None))
        if sorted(seen) != sorted(manifest["modified_files"]):
            raise ValueError("Manifest file list does not match its entries.")
        if conflicts:
            result = {"applied": False, "status": "CONFLICT", "conflicts": sorted(conflicts),
                      "recovery_errors": []}
            receipt = artifact_dir / "promotion_result.json"
            try:
                receipt.write_text(json.dumps(result, indent=2), encoding="utf-8")
                result["record_path"] = str(receipt)
            except OSError as exc:
                result["record_error"] = str(exc)
            return result

        receipt = manifest_file.parent / "promotion_result.json"
        def record(result):
            result = dict(result)
            result["record_path"] = str(receipt)
            try:
                receipt.write_text(json.dumps(result, indent=2), encoding="utf-8")
            except OSError as exc:
                result["record_error"] = str(exc)
            return result

        # Stage every replacement before touching destination files. Recheck each
        # baseline immediately before replace; rollback only files still containing
        # our staged bytes so concurrent edits are never overwritten.
        staged = []
        created_dirs = []
        applied = []
        attempting = None
        try:
            for relative, destination, content, old, old_stat in destinations:
                missing = []
                parent = destination.parent
                while not parent.exists():
                    missing.append(parent)
                    parent = parent.parent
                for directory in reversed(missing):
                    directory.mkdir()
                    created_dirs.append(directory)
                fd, name = tempfile.mkstemp(prefix=".codepilot-stage-", dir=destination.parent)
                staged_path = Path(name)
                new_digest = _digest(content.encode("utf-8"))
                item = (relative, destination, staged_path, old, old_stat, new_digest)
                staged.append(item)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content.encode("utf-8"))
                    stream.flush()
                    os.fsync(stream.fileno())
                if old_stat is not None:
                    os.chmod(staged_path, stat.S_IMODE(old_stat.st_mode))

            for relative, destination, staged_path, old, old_stat, new_digest in staged:
                current = destination.read_bytes() if destination.is_file() else None
                if _digest(current) != _digest(old):
                    raise RuntimeError(f"Destination changed after validation: {relative}")
                attempting = (relative, destination, old, old_stat, new_digest)
                os.replace(staged_path, destination)
                applied.append((relative, destination, old, old_stat, new_digest))
                attempting = None
        except Exception as exc:
            recovery_errors = []
            recovered = []
            # Include a replace which raised after changing its destination.
            if attempting is not None:
                relative, destination, old, old_stat, new_digest = attempting
                current = destination.read_bytes() if destination.is_file() else None
                if _digest(current) == new_digest and all(item[1] != destination for item in applied):
                    applied.append(attempting)
            for relative, destination, old, old_stat, new_digest in reversed(applied):
                try:
                    current = destination.read_bytes() if destination.is_file() else None
                    if _digest(current) != new_digest:
                        raise RuntimeError("Destination changed during recovery; preserving concurrent edit.")
                    if old is None:
                        destination.unlink()
                    else:
                        fd, name = tempfile.mkstemp(prefix=".codepilot-recover-", dir=destination.parent)
                        with os.fdopen(fd, "wb") as stream:
                            stream.write(old)
                            stream.flush()
                            os.fsync(stream.fileno())
                        if old_stat is not None:
                            os.chmod(name, stat.S_IMODE(old_stat.st_mode))
                            os.utime(name, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
                        os.replace(name, destination)
                    recovered.append(relative)
                except Exception as recovery_exc:
                    recovery_errors.append(f"{relative}: {recovery_exc}")
            for path in staged:
                try:
                    path[2].unlink(missing_ok=True)
                except OSError:
                    pass
            for directory in reversed(created_dirs):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            status = "FAILED_RECOVERED" if not recovery_errors else "RECOVERY_FAILED"
            return record({"applied": False, "status": status, "error": str(exc),
                           "recovered_files": sorted(recovered), "recovery_errors": recovery_errors})

        return record({"applied": True, "status": "APPLIED",
                       "files": [str(path[1]) for path in staged], "recovery_errors": []})

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
