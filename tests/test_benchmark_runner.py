import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks import runner


class TestBenchmarkRunner(unittest.TestCase):
    def test_cli_writes_valid_json_and_nonzero_on_failure(self):
        fake = {"id": "BENCH-001", "status": "FAILED"}
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "report.json"
            with patch.object(runner, "run_one", return_value=fake):
                code = runner.main(
                    ["--scenario", "BENCH-001", "--output", str(destination)]
                )
            self.assertEqual(code, 1)
            parsed = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(parsed["results"], [fake])
            self.assertEqual(parsed["failed"], 1)

    def test_scenario_selection_rejects_unknown_ids(self):
        with self.assertRaises(SystemExit):
            runner.main(["--scenario", "BENCH-999"])

    def test_repair_acceptance_compares_checkpoint_to_final_snapshot(self):
        before = {"repair_loop_temp.py": "before-hash"}
        after = {"repair_loop_temp.py": "after-hash"}
        self.assertTrue(
            runner._changed_from_checkpoint(before, after, "repair_loop_temp.py")
        )
        self.assertFalse(
            runner._changed_from_checkpoint(before, before, "repair_loop_temp.py")
        )

    def test_repair_scenarios_seed_bug_fixture_without_touching_source_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_workspace = root / "source"
            source_workspace.mkdir()
            source = source_workspace / "repair_loop_temp.py"
            original = 'def divide(a, b):\n    return "already fixed"\n'
            source.write_text(original, encoding="utf-8")
            isolated_workspace = root / "isolated"
            import shutil

            shutil.copytree(source_workspace, isolated_workspace)
            runner._prepare_scenario_workspace("BENCH-002", isolated_workspace)
            self.assertEqual(
                (isolated_workspace / "repair_loop_temp.py").read_text(
                    encoding="utf-8"
                ),
                runner.DIVISION_ZERO_BUG_FIXTURE,
            )
            self.assertEqual(source.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
