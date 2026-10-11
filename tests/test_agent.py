import unittest
from unittest.mock import patch

from tools.filesystem import list_files, read_file


class TestCodePilotFilesystem(unittest.TestCase):

    def test_workspace_files_exist(self):
        files = list_files()

        self.assertIsInstance(files, list)
        self.assertTrue(
            any("buggy.py" in file for file in files),
            "buggy.py should exist in the workspace"
        )

    def test_read_calculator(self):
        code = read_file("calculator.py")

        self.assertIsInstance(code, str)
        self.assertIn("def add", code)
        self.assertIn("def subtract", code)

    def test_read_buggy_program(self):
        code = read_file("buggy.py")

        self.assertIsInstance(code, str)
        self.assertIn("def divide", code)

    def test_missing_file(self):
        result = read_file("file_that_does_not_exist.py")

        self.assertIn("ERROR", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)