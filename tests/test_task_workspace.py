import json
import tempfile
import unittest
from pathlib import Path

from agent.task_workspace import TaskWorkspaceManager, _safe_relative


class TestTaskWorkspaceManager(unittest.TestCase):
    def test_copy_is_separate_and_cleanup_stays_in_temporary_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = root / "primary"
            temp_root = root / "tasks"
            artifacts = root / "artifacts"
            primary.mkdir()
            (primary / "a.py").write_text("original\n", encoding="utf-8")
            manager = TaskWorkspaceManager(primary, artifacts, temp_root)
            workspace = manager.create("task-abc123")
            (workspace / "a.py").write_text("task edit\n", encoding="utf-8")
            self.assertEqual((primary / "a.py").read_text(encoding="utf-8"), "original\n")
            result = manager.cleanup()
            self.assertTrue(result["success"])
            self.assertFalse(workspace.exists())
            self.assertTrue(primary.exists())

    def test_success_artifact_is_reviewable_and_requires_explicit_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = root / "primary"
            primary.mkdir()
            (primary / "a.py").write_text("old\n", encoding="utf-8")
            manager = TaskWorkspaceManager(primary, root / "artifacts", root / "tmp")
            workspace = manager.create("task-review")
            (workspace / "a.py").write_text("new\n", encoding="utf-8")
            artifact = manager.create_review_artifact(
                "task-review", ["a.py"], "SUCCESS", {"verified": True})
            manifest = json.loads(Path(artifact["manifest_path"]).read_text(encoding="utf-8"))
            self.assertEqual(manifest["modified_files"], ["a.py"])
            self.assertIn("-old", Path(artifact["patch_path"]).read_text(encoding="utf-8"))
            self.assertTrue(manifest["promotion_requires_explicit_approval"])
            with self.assertRaises(PermissionError):
                manager.apply_reviewed_artifact(artifact["manifest_path"])
            applied = manager.apply_reviewed_artifact(artifact["manifest_path"], approved=True)
            self.assertTrue(applied["applied"])
            self.assertEqual((primary / "a.py").read_text(encoding="utf-8"), "new\n")

    def test_new_primary_edit_conflicts_without_partial_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = root / "primary"
            primary.mkdir()
            (primary / "a.py").write_text("old a\n", encoding="utf-8")
            (primary / "b.py").write_text("old b\n", encoding="utf-8")
            manager = TaskWorkspaceManager(primary, root / "artifacts", root / "tmp")
            workspace = manager.create("task-conflict")
            (workspace / "a.py").write_text("task a\n", encoding="utf-8")
            (workspace / "b.py").write_text("task b\n", encoding="utf-8")
            artifact = manager.create_review_artifact("task-conflict", ["a.py", "b.py"], "SUCCESS")
            (primary / "b.py").write_text("user edit\n", encoding="utf-8")
            result = manager.apply_reviewed_artifact(artifact["manifest_path"], approved=True)
            self.assertFalse(result["applied"])
            self.assertEqual(result["conflicts"], ["b.py"])
            self.assertEqual((primary / "a.py").read_text(encoding="utf-8"), "old a\n")
            self.assertEqual((primary / "b.py").read_text(encoding="utf-8"), "user edit\n")

    def test_traversal_and_symlink_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.txt"
            outside.write_text("secret", encoding="utf-8")
            with self.assertRaises(ValueError):
                _safe_relative(root, "../outside.txt")
            link = root / "escape"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("Symlink creation is unavailable on this host")
            with self.assertRaises(ValueError):
                _safe_relative(root, "escape")

    def test_cleanup_refuses_target_outside_temporary_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "precious"
            outside.mkdir()
            marker = outside / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            manager = TaskWorkspaceManager(root, root / "artifacts", root / "tmp")
            manager.task_root = outside
            result = manager.cleanup()
            self.assertFalse(result["success"])
            self.assertTrue(marker.exists())


if __name__ == "__main__":
    unittest.main()
