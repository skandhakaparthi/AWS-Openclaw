import inspect
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import git_manager


class TestGitManagerInTemporaryRepository(unittest.TestCase):
    """Checkpoint/rollback behavior is verified only in throwaway repos.

    The user's working repository is never a rollback target in these
    tests: every git call is made with cwd=<temp dir>, and after each test
    the project repository's HEAD must be unchanged.
    """

    PROJECT_ROOT = Path(git_manager.__file__).resolve().parent.parent

    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="codepilot-git-test-")
        # Registered first -> runs last (LIFO): delete after other cleanups.
        self.addCleanup(__import__("shutil").rmtree, self.repo, True)
        self.head_before = self._project_head()
        self.addCleanup(self._assert_project_repo_untouched)

        self._git("init")
        self._git("config", "user.email", "sandbox-test@example.com")
        self._git("config", "user.name", "Sandbox Test")
        self._git("config", "commit.gpgsign", "false")

        Path(self.repo, "app.py").write_text("value = 1\n", encoding="utf-8")
        self._git("add", "app.py")
        self._git("commit", "-m", "initial")

    def _git(self, *args):
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=self.repo,
            timeout=30,
        )
        if result.returncode != 0:
            raise AssertionError(f"git {args} failed: {result.stderr}")
        return result.stdout.strip()

    def _project_head(self):
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=self.PROJECT_ROOT,
            timeout=30,
        )
        return result.stdout.strip()

    def _assert_project_repo_untouched(self):
        self.assertEqual(self._project_head(), self.head_before,
                         "project repository HEAD changed during test")

    def test_checkpoint_requires_clean_tree(self):
        Path(self.repo, "app.py").write_text("value = 2\n", encoding="utf-8")

        with self.assertRaises(RuntimeError):
            git_manager.create_checkpoint(cwd=self.repo)

    def test_checkpoint_creates_tag_in_temp_repo(self):
        name = git_manager.create_checkpoint(cwd=self.repo)

        self.assertTrue(name.startswith("agent-checkpoint-"))
        tags = self._git("tag", "--list").splitlines()
        self.assertIn(name, tags)

    def test_rollback_restores_file_only_in_temp_repo(self):
        checkpoint = git_manager.create_checkpoint(cwd=self.repo)

        Path(self.repo, "app.py").write_text("value = 999\n", encoding="utf-8")
        git_manager.rollback(checkpoint, cwd=self.repo)

        restored = Path(self.repo, "app.py").read_text(encoding="utf-8")
        self.assertEqual(restored, "value = 1\n")

    def test_rollback_preserves_untracked_files(self):
        checkpoint = git_manager.create_checkpoint(cwd=self.repo)

        untracked = Path(self.repo, "notes.txt")
        untracked.write_text("keep me\n", encoding="utf-8")
        Path(self.repo, "app.py").write_text("value = 3\n", encoding="utf-8")

        git_manager.rollback(checkpoint, cwd=self.repo)

        self.assertTrue(untracked.exists(),
                        "rollback must not delete untracked files")

    def test_get_current_commit_matches_head(self):
        head = self._git("rev-parse", "HEAD")
        self.assertEqual(git_manager.get_current_commit(cwd=self.repo), head)

    def test_checkpoint_api_backward_compatible(self):
        for function in (git_manager.create_checkpoint,
                         git_manager.rollback,
                         git_manager.get_current_commit):
            parameters = inspect.signature(function).parameters
            self.assertIn("cwd", parameters)
            self.assertIsNone(parameters["cwd"].default)


if __name__ == "__main__":
    unittest.main(verbosity=2)
