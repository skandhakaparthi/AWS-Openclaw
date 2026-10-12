import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock

from agent.react_agent import ReActAgent
from agent.state import TaskState
from tools.filesystem import WORKSPACE
from tools.tool_registry import ToolRegistry


class TestReflection(unittest.TestCase):
    def setUp(self):
        self.agent = ReActAgent()
        self.agent.task_state = TaskState("test-task")
        self.agent.tools = Mock()
        self.agent.llm = Mock()
        self.agent.safety.reset()

    def test_success_requires_verified_zero_exit(self):
        decision = self.agent.reflect(
            {"success": True, "return_code": 0, "output": "ok", "error": "",
             "execution_mode": "docker-sandbox"}, "a.py", 1)
        self.assertEqual(decision, "SUCCESS")
        record = self.agent.task_state.state["history"][-1]
        self.assertTrue(record["details"]["acceptance_check"]["verified"])

    def test_missing_result_is_not_success(self):
        self.assertNotEqual(self.agent.reflect(None, "a.py", 1), "SUCCESS")

    def test_docker_unavailable_stops_without_repair(self):
        decision = self.agent.reflect(
            {"success": False, "return_code": -2, "error": "Docker unavailable",
             "execution_mode": "docker-unavailable"}, "a.py", 1)
        self.assertEqual(decision, "INFRA_ERROR")
        self.agent.llm.analyze_error.assert_not_called()
        self.agent.tools.read_file.assert_not_called()

    def test_missing_image_stops_without_repair(self):
        decision = self.agent.reflect(
            {"success": False, "return_code": -2, "error": "missing",
             "execution_mode": "image-missing"}, "a.py", 1)
        self.assertEqual(decision, "INFRA_ERROR")
        self.agent.llm.analyze_error.assert_not_called()

    def test_timeout_is_classified_and_stops(self):
        decision = self.agent.reflect(
            {"success": False, "return_code": -1, "timed_out": True,
             "error": "timed out", "execution_mode": "docker-sandbox"}, "a.py", 1)
        self.assertEqual(decision, "TIMEOUT")

    def test_syntax_error_is_not_success(self):
        self.agent.tools.read_file.return_value = "broken ("
        self.agent.llm.analyze_error.return_value = {"action": "stop", "reason": "inspect"}
        decision = self.agent.reflect(
            {"success": False, "return_code": 1, "error": "SyntaxError: invalid syntax",
             "execution_mode": "docker-sandbox"}, "a.py", 1)
        self.assertEqual(decision, "STOP")
        self.assertEqual(self.agent.task_state.state["error_classification"], "SYNTAX_ERROR")

    def test_failed_assertion_cannot_succeed(self):
        self.agent.tools.read_file.return_value = "assert False"
        self.agent.llm.analyze_error.return_value = {"action": "stop", "reason": "failure"}
        decision = self.agent.reflect(
            {"success": False, "return_code": 1, "error": "AssertionError",
             "execution_mode": "docker-sandbox"}, "a.py", 1)
        self.assertEqual(decision, "STOP")
        self.assertEqual(self.agent.task_state.state["error_classification"], "ASSERTION_FAILURE")

    def test_permission_rejection_stops_safely(self):
        decision = self.agent.reflect(
            {"success": False, "return_code": -2, "error": "denied",
             "execution_mode": "path-rejected"}, "../a.py", 1)
        self.assertEqual(decision, "PERMISSION_ERROR")
        self.agent.llm.analyze_error.assert_not_called()

    def test_first_repair_is_attempted_for_runtime_error(self):
        self.agent.tools.read_file.side_effect = ["def f(): return 1 / 0\n", "def f(): return 0\n"]
        self.agent.llm.analyze_error.return_value = {"action": "fix", "reason": "known"}
        self.agent.llm.generate_fix.return_value = "def f(): return 0\n"
        decision = self.agent.reflect(
            {"success": False, "return_code": 1, "error": "ZeroDivisionError",
             "execution_mode": "docker-sandbox"}, "a.py", 1)
        self.assertEqual(decision, "FIX")
        self.agent.tools.write_file.assert_called_once()

    def test_repeated_equivalent_errors_stop(self):
        self.agent.tools.read_file.side_effect = ["def f(): return 1 / 0\n", "def f(): return 1 / 0\n",
                                                  "def f(): return 1 / 0\n"]
        self.agent.llm.analyze_error.return_value = {"action": "fix", "reason": "known"}
        self.agent.llm.generate_fix.return_value = "def f(): return 1 / 0\n"
        failed = {"success": False, "return_code": 1, "error": "ZeroDivisionError",
                  "execution_mode": "docker-sandbox"}
        self.assertEqual(self.agent.reflect(failed, "a.py", 1), "FIX")
        self.assertEqual(self.agent.reflect(failed, "a.py", 2), "MAX_ITERATIONS")
        self.assertEqual(self.agent.task_state.state["error_classification"], "REPEATED_ERROR")


class TestWorkspaceIsolation(unittest.TestCase):
    def test_registry_write_is_confined_to_injected_workspace(self):
        original = (WORKSPACE / "calculator.py").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            isolated = Path(directory)
            (isolated / "calculator.py").write_text("before\n", encoding="utf-8")
            registry = ToolRegistry(workspace=isolated)
            registry.write_file("calculator.py", "changed\n")
            self.assertEqual((isolated / "calculator.py").read_text(encoding="utf-8"), "changed\n")
        self.assertEqual((WORKSPACE / "calculator.py").read_text(encoding="utf-8"), original)

    def test_scoped_rollback_restores_agent_edit_and_keeps_other_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "target.py").write_text("original\n", encoding="utf-8")
            (root / "notes.txt").write_text("keep\n", encoding="utf-8")
            agent = ReActAgent(workspace=root)
            agent._original_task_files = {"target.py": "original\n"}
            agent._expected_task_files = {"target.py": "agent edit\n"}
            registry = ToolRegistry(workspace=root)
            registry.write_file("target.py", "agent edit\n")
            result = agent.rollback_task_changes()
            self.assertTrue(result["verified"])
            self.assertEqual(result["restored_files"], ["target.py"])
            self.assertEqual((root / "target.py").read_text(encoding="utf-8"), "original\n")
            self.assertEqual((root / "notes.txt").read_text(encoding="utf-8"), "keep\n")

    def test_scoped_rollback_preserves_external_edit_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "target.py").write_text("external edit\n", encoding="utf-8")
            agent = ReActAgent(workspace=root)
            agent._original_task_files = {"target.py": "original\n"}
            agent._expected_task_files = {"target.py": "agent edit\n"}
            agent.tools = ToolRegistry(workspace=root)
            result = agent.rollback_task_changes()
            self.assertFalse(result["verified"])
            self.assertEqual(result["conflicts"], ["target.py"])
            self.assertEqual((root / "target.py").read_text(encoding="utf-8"), "external edit\n")


if __name__ == "__main__":
    unittest.main()
