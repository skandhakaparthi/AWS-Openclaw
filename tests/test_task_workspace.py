import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent.task_workspace import TaskWorkspaceManager, _safe_relative


class TestTaskWorkspaceManager(unittest.TestCase):
    def _two_file_artifact(self, root):
        primary = root / "primary"
        primary.mkdir()
        (primary / "a.py").write_text("old a\n", encoding="utf-8")
        (primary / "b.py").write_text("old b\n", encoding="utf-8")
        manager = TaskWorkspaceManager(primary, root / "artifacts", root / "tmp")
        workspace = manager.create("task-multi")
        (workspace / "a.py").write_text("new a\n", encoding="utf-8")
        (workspace / "b.py").write_text("new b\n", encoding="utf-8")
        artifact = manager.create_review_artifact("task-multi", ["a.py", "b.py"], "SUCCESS")
        return primary, manager, artifact

    def test_multi_file_promotion_and_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary, manager, artifact = self._two_file_artifact(root)
            result = manager.apply_reviewed_artifact(artifact["manifest_path"], approved=True)
            self.assertTrue(result["applied"])
            self.assertEqual(result["status"], "APPLIED")
            self.assertEqual((primary / "a.py").read_text(), "new a\n")
            self.assertEqual((primary / "b.py").read_text(), "new b\n")
            self.assertEqual(json.loads(Path(result["record_path"]).read_text())["status"], "APPLIED")

    def test_failure_before_first_replace_recovers_original_state(self):
        with tempfile.TemporaryDirectory() as directory:
            primary, manager, artifact = self._two_file_artifact(Path(directory))
            with mock.patch("agent.task_workspace.os.replace", side_effect=OSError("injected")):
                result = manager.apply_reviewed_artifact(artifact["manifest_path"], approved=True)
            self.assertEqual(result["status"], "FAILED_RECOVERED")
            self.assertEqual((primary / "a.py").read_text(), "old a\n")
            self.assertEqual((primary / "b.py").read_text(), "old b\n")

    def test_midway_failure_rolls_back_only_transaction_files(self):
        with tempfile.TemporaryDirectory() as directory:
            primary, manager, artifact = self._two_file_artifact(Path(directory))
            unrelated = primary / "user.txt"
            unrelated.write_text("keep", encoding="utf-8")
            real_replace = __import__("os").replace
            calls = 0
            def fail_second(src, dst):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected midway")
                return real_replace(src, dst)
            with mock.patch("agent.task_workspace.os.replace", side_effect=fail_second):
                result = manager.apply_reviewed_artifact(artifact["manifest_path"], approved=True)
            self.assertEqual(result["status"], "FAILED_RECOVERED")
            self.assertEqual((primary / "a.py").read_text(), "old a\n")
            self.assertEqual((primary / "b.py").read_text(), "old b\n")
            self.assertEqual(unrelated.read_text(), "keep")

    def test_recovery_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            primary, manager, artifact = self._two_file_artifact(Path(directory))
            real_replace = __import__("os").replace
            calls = 0
            def fail_promotion_and_recovery(src, dst):
                nonlocal calls
                calls += 1
                if calls in (2, 3):
                    raise OSError("injected failure")
                return real_replace(src, dst)
            with mock.patch("agent.task_workspace.os.replace", side_effect=fail_promotion_and_recovery):
                result = manager.apply_reviewed_artifact(artifact["manifest_path"], approved=True)
            self.assertEqual(result["status"], "RECOVERY_FAILED")
            self.assertTrue(result["recovery_errors"])
            self.assertEqual(json.loads(Path(result["record_path"]).read_text())["status"], "RECOVERY_FAILED")

    def test_manifest_path_traversal_and_symlinks_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary, manager, artifact = self._two_file_artifact(root)
            manifest_path = Path(artifact["manifest_path"])
            manifest = json.loads(manifest_path.read_text())
            manifest["files"][0]["path"] = "../outside"
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                manager.apply_reviewed_artifact(manifest_path, approved=True)

    def test_unsafe_destination_symlink_is_rejected_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary, manager, artifact = self._two_file_artifact(root)
            original = Path.is_symlink
            def pretend_symlink(path):
                if path == primary / "b.py":
                    return True
                return original(path)
            with mock.patch.object(Path, "is_symlink", pretend_symlink):
                with self.assertRaises(ValueError):
                    manager.apply_reviewed_artifact(artifact["manifest_path"], approved=True)
            self.assertEqual((primary / "a.py").read_text(), "old a\n")
            self.assertEqual((primary / "b.py").read_text(), "old b\n")

    def test_user_edit_after_artifact_generation_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            primary, manager, artifact = self._two_file_artifact(Path(directory))
            (primary / "b.py").write_text("concurrent edit\n")
            result = manager.apply_reviewed_artifact(artifact["manifest_path"], approved=True)
            self.assertEqual(result["status"], "CONFLICT")
            self.assertEqual((primary / "a.py").read_text(), "old a\n")
            self.assertEqual((primary / "b.py").read_text(), "concurrent edit\n")
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
