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
                code = runner.main(["--scenario", "BENCH-001", "--output", str(destination)])
            self.assertEqual(code, 1)
            parsed = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(parsed["results"], [fake])
            self.assertEqual(parsed["failed"], 1)

    def test_scenario_selection_rejects_unknown_ids(self):
        with self.assertRaises(SystemExit):
            runner.main(["--scenario", "BENCH-999"])


if __name__ == "__main__":
    unittest.main()
