"""Local dashboard API tests with temporary workspaces and no Docker dependency."""

import json
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent.task_workspace import TaskWorkspaceManager
from llm.client import LLMClient
from web.app import DemoService, create_server


class FakeState:
    def __init__(self, execution, classification):
        self.state = {
            "status": "FAILED",
            "iterations": 1,
            "plan": ["Run check"],
            "files_changed": [],
            "latest_execution_result": execution,
            "latest_test_result": {"verified": False},
            "error_classification": classification,
            "task_id": uuid.uuid4().hex,
            "request_metadata": None,
            "tool_invocations": [],
        }


class FakeAgent:
    def __init__(
        self, workspace, state_dir, execution, classification, successful=False
    ):
        self.workspace = Path(workspace)
        self.state_dir = Path(state_dir)
        self.request = None
        self.executed_source = None
        self.task_state = FakeState(execution, classification)
        self.successful = successful
        self.history = []

    def run(self, task, filename):
        self.request = (task, filename)
        self.executed_source = (self.workspace / filename).read_text(encoding="utf-8")
        from hashlib import sha256

        self.task_state.state["request_metadata"] = {
            "sha256": sha256(task.encode("utf-8")).hexdigest()
        }
        self.task_state.state["tool_invocations"].append(
            {"tool": "run_python", "filename": filename}
        )
        self.history.append({"phase": "TASK", "action": task})
        if self.successful:
            self.task_state.state.update(
                {
                    "status": "SUCCESS",
                    "latest_test_result": {"verified": True},
                    "latest_execution_result": {
                        "success": True,
                        "output": "agent execution passed",
                        "error": "",
                        "return_code": 0,
                        "execution_mode": "docker-sandbox",
                        "timed_out": False,
                    },
                }
            )
        return {"status": self.task_state.state["status"]}


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codepilot-web-test-")
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "factorial.py").write_text(
            "def factorial(n):\n    return 1 if n < 2 else n * factorial(n - 1)\n",
            encoding="utf-8",
        )
        (self.workspace / "repair_loop_temp.py").write_text(
            "def divide(a, b):\n    return a / b\n", encoding="utf-8"
        )
        (self.workspace / "demo.py").write_text("print('before')\n", encoding="utf-8")
        (self.root / "outside.py").write_text("secret\n", encoding="utf-8")
        self.agents = []
        self.fake_execution = {
            "success": False,
            "output": "",
            "error": "Docker unavailable",
            "return_code": -2,
            "execution_mode": "docker-unavailable",
            "timed_out": False,
        }
        self.fake_classification = "INFRA_ERROR"
        self.fake_success = False
        self.service = DemoService(
            self.workspace, self.root / "artifacts", agent_factory=self.make_agent
        )
        self.server = create_server("127.0.0.1", 0, self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def make_agent(self, workspace, state_dir):
        agent = FakeAgent(
            workspace,
            state_dir,
            self.fake_execution,
            self.fake_classification,
            successful=self.fake_success,
        )
        self.agents.append(agent)
        return agent

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path, method="GET", payload=None, token=True, origin=True):
        data = None if payload is None else json.dumps(payload).encode()
        headers = {"Connection": "close"}
        if data is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(data))
        if origin:
            headers["Origin"] = self.base
            headers["Sec-Fetch-Site"] = "same-origin"
        if token:
            headers["X-CodePilot-CSRF"] = self.service.csrf_token
        req = Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=3) as response:
                raw = response.read()
                content_type = response.headers.get("Content-Type", "")
                return response.status, json.loads(
                    raw
                ) if "json" in content_type else raw.decode()
        except HTTPError as exc:
            raw = exc.read()
            status = exc.code
            exc.close()
            return status, json.loads(raw) if raw else None

    def seed_review(self):
        manager = TaskWorkspaceManager(self.workspace, self.root / "artifacts")
        task_workspace = manager.create("task-webtest")
        (task_workspace / "demo.py").write_text("print('after')\n", encoding="utf-8")
        artifact = manager.create_review_artifact(
            "task-webtest", ["demo.py"], "SUCCESS", {"verified": True}
        )
        manager.cleanup()
        key = "a" * 32
        self.service._tasks[key] = {
            "task_id": key,
            "task": "demo acceptance",
            "filename": "demo.py",
            "status": "SUCCESS",
            "created_at": time.time(),
            "iteration": 1,
            "plan": ["Run acceptance check"],
            "logs": [],
            "execution": {
                "success": True,
                "output": "ok",
                "error": "",
                "return_code": 0,
                "execution_mode": "docker-sandbox",
                "timed_out": False,
            },
            "verification": {"verified": True, "return_code": 0},
            "error_classification": "SUCCESS",
            "modified_files": ["demo.py"],
            "message": "Verified",
            "promotion_status": "NOT_REQUESTED",
            "manifest_path": artifact["manifest_path"],
            "agent": None,
            "acceptance": {
                "verified": True,
                "profile": "demo-v1",
                "criteria": ["test"],
            },
        }
        self.service._order.append(key)
        return key

    def test_startup_dashboard_and_health(self):
        status, html = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("CodePilot", html)
        self.assertNotIn("__CODEPILOT_CSRF_TOKEN__", html)
        health = self.request("/api/health")[1]
        self.assertEqual(health["llm_mode"], "mock")
        self.assertEqual(
            self.request("/api/session")[1]["csrf_token"], self.service.csrf_token
        )
        self.assertEqual(
            self.request("/api/workspace-files")[1]["files"],
            ["demo.py", "factorial.py", "repair_loop_temp.py"],
        )

    def test_submission_validation_and_unknown_task(self):
        for payload in (
            {"task": " ", "filename": "factorial.py"},
            {"task": "x" * 1501, "filename": "factorial.py"},
            {"task": "factorial", "filename": "../outside.py"},
            {"task": "run", "filename": "outside.py"},
            {"task": "factorial", "filename": "factorial.py", "extra": True},
            {"task": "factorial", "filename": "repair_loop_temp.py"},
            {"task": "some unrelated request", "filename": "factorial.py"},
        ):
            self.assertEqual(self.request("/api/tasks", "POST", payload)[0], 400)
        self.assertEqual(self.request("/api/tasks/" + "0" * 32)[0], 404)
        self.assertEqual(self.request("/api/tasks/../README.md")[0], 404)

    def test_write_requires_local_origin_and_csrf(self):
        payload = {
            "task": "Handle division by zero safely.",
            "filename": "repair_loop_temp.py",
        }
        self.assertEqual(
            self.request("/api/tasks", "POST", payload, token=False)[0], 403
        )
        self.assertEqual(
            self.request("/api/tasks", "POST", payload, origin=False)[0], 403
        )
        status, submitted = self.request("/api/tasks", "POST", payload)
        self.assertEqual(status, 202)
        self.wait_for_task(submitted["task_id"])

    def test_diff_rejection_and_explicit_approval(self):
        key = self.seed_review()
        status, diff = self.request(f"/api/tasks/{key}/diff")
        self.assertEqual(status, 200)
        self.assertIn("print('after')", diff["patch"])
        self.assertEqual(
            self.request(f"/api/tasks/{key}/promote", "POST", {"approved": False})[0],
            403,
        )
        self.assertIn(
            "before", (self.workspace / "demo.py").read_text(encoding="utf-8")
        )
        self.assertEqual(self.request(f"/api/tasks/{key}/reject", "POST", {})[0], 200)
        self.assertIn(
            "before", (self.workspace / "demo.py").read_text(encoding="utf-8")
        )

    def test_explicit_approval_promotes_reviewed_patch(self):
        key = self.seed_review()
        status, result = self.request(
            f"/api/tasks/{key}/promote", "POST", {"approved": True}
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "APPLIED")
        self.assertIn("after", (self.workspace / "demo.py").read_text(encoding="utf-8"))

    def test_rejects_non_loopback_bind(self):
        with self.assertRaises(ValueError):
            create_server("0.0.0.0", 0, self.service)

    def test_mock_mode_is_default_and_docker_unavailable_fails_closed(self):
        self.assertEqual(LLMClient(mode="mock").mode, "mock")
        default_service = DemoService(self.workspace, self.root / "default-artifacts")
        self.assertEqual(
            default_service.agent_factory(self.workspace, self.root / "state").llm.mode,
            "mock",
        )
        status, submitted = self.request(
            "/api/tasks",
            "POST",
            {
                "task": "Handle division by zero safely.",
                "filename": "repair_loop_temp.py",
            },
        )
        self.assertEqual(status, 202)
        snapshot = self.wait_for_task(submitted["task_id"])
        self.assertEqual(snapshot["status"], "FAILED")
        self.assertIn("failed closed", snapshot["message"])
        self.assertFalse(snapshot["verification"]["verified"])

    def test_timeout_is_reported_without_success(self):
        self.fake_execution = {
            "success": False,
            "output": "",
            "error": "timed out",
            "return_code": -1,
            "execution_mode": "docker-sandbox",
            "timed_out": True,
        }
        self.fake_classification = "TIMEOUT"
        _, submitted = self.request(
            "/api/tasks",
            "POST",
            {
                "task": "Handle division by zero safely.",
                "filename": "repair_loop_temp.py",
            },
        )
        snapshot = self.wait_for_task(submitted["task_id"])
        self.assertEqual(snapshot["status"], "TIMED_OUT")
        self.assertFalse(snapshot["verification"]["verified"])

    def wait_for_task(self, task_id):
        deadline = time.time() + 2
        snapshot = None
        while time.time() < deadline:
            _, snapshot = self.request(f"/api/tasks/{task_id}")
            if snapshot["status"] != "RUNNING":
                break
            time.sleep(0.01)
        return snapshot

    def test_factorial_request_is_rejected_with_incompatible_selected_file(self):
        status, response = self.request(
            "/api/tasks",
            "POST",
            {
                "task": "Implement factorial and test 0, 1, and 5.",
                "filename": "repair_loop_temp.py",
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("requires factorial.py", response["error"])
        self.assertEqual(self.service._tasks, {})
        self.assertEqual(self.agents, [])

    def test_factorial_task_uses_scoped_fixture_and_fresh_artifacts(self):
        self.fake_success = True
        real_executions = []

        def verify_in_sandbox(filename, workspace):
            verifier = (Path(workspace) / filename).read_text(encoding="utf-8")
            if filename == "verify_factorial.py":
                fixture = (Path(workspace) / "factorial.py").read_text(encoding="utf-8")
                for assertion in (
                    "factorial(0) == 1",
                    "factorial(1) == 1",
                    "factorial(5) == 120",
                ):
                    self.assertIn(assertion, fixture)
                    self.assertIn(assertion, verifier)
                real_executions.append((Path(workspace).resolve(), fixture))
                marker = "CODEPILOT_ACCEPTANCE:FACTORIAL:0=1,1=1,5=120\n"
            else:
                self.assertEqual(filename, "verify_division_zero.py")
                real_executions.append((Path(workspace).resolve(), verifier))
                marker = "CODEPILOT_ACCEPTANCE:DIVISION_ZERO:10/0=message,10/2=5\n"
            return {
                "success": True,
                "output": marker,
                "error": "",
                "return_code": 0,
                "execution_mode": "docker-sandbox",
                "timed_out": False,
            }

        from unittest.mock import patch

        with patch("web.app.run_python_file", side_effect=verify_in_sandbox):
            status, first = self.request(
                "/api/tasks",
                "POST",
                {
                    "task": "Handle division by zero safely.",
                    "filename": "repair_loop_temp.py",
                },
            )
            self.assertEqual(status, 202)
            first_result = self.wait_for_task(first["task_id"])
            self.assertEqual(first_result["status"], "SUCCESS")

            factorial_task = "Implement a factorial function and test 0, 1, and 5."
            status, second = self.request(
                "/api/tasks",
                "POST",
                {"task": factorial_task, "filename": "factorial.py"},
            )
            self.assertEqual(status, 202)
            second_result = self.wait_for_task(second["task_id"])

        self.assertEqual(second_result["status"], "SUCCESS")
        self.assertEqual(second_result["task"], factorial_task)
        self.assertEqual(second_result["filename"], "factorial.py")
        self.assertEqual(second_result["acceptance"]["verified"], True)
        self.assertEqual(
            second_result["acceptance"]["criteria"],
            ["factorial(0) == 1", "factorial(1) == 1", "factorial(5) == 120"],
        )
        self.assertNotEqual(first_result["workspace"], second_result["workspace"])
        self.assertNotEqual(
            first_result["task_state_id"], second_result["task_state_id"]
        )
        self.assertNotEqual(self.agents[0].state_dir, self.agents[1].state_dir)
        self.assertEqual(
            self.agents[0].request,
            ("Handle division by zero safely.", "repair_loop_temp.py"),
        )
        self.assertEqual(self.agents[1].request, (factorial_task, "factorial.py"))
        self.assertEqual(len(real_executions), 2)
        self.assertIn("FACTORIAL_FIXTURE_CHECKS_PASSED", self.agents[1].executed_source)
        status, diff = self.request(f"/api/tasks/{second['task_id']}/diff")
        self.assertEqual(status, 200)
        self.assertIn("factorial(5) == 120", diff["patch"])
        self.assertNotIn("divide", diff["patch"].lower())
        self.assertNotIn("Cannot divide by zero", diff["patch"])
        self.assertFalse(
            any("division" in str(item).lower() for item in second_result["logs"])
        )
        manifest_path = self.service._tasks[second["task_id"]]["manifest_path"]
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        self.assertEqual(manifest["test_result"]["requested_task"], factorial_task)
        self.assertEqual(manifest["test_result"]["selected_file"], "factorial.py")
        self.assertTrue(manifest["test_result"]["task_acceptance"]["verified"])

    def test_factorial_cannot_succeed_with_division_output_or_promote_a_patch(self):
        self.fake_success = True
        from unittest.mock import patch

        with patch(
            "web.app.run_python_file",
            return_value={
                "success": True,
                "output": "Cannot divide by zero",
                "error": "",
                "return_code": 0,
                "execution_mode": "docker-sandbox",
                "timed_out": False,
            },
        ):
            status, submitted = self.request(
                "/api/tasks",
                "POST",
                {
                    "task": "Implement factorial and test 0, 1, and 5.",
                    "filename": "factorial.py",
                },
            )
            self.assertEqual(status, 202)
            result = self.wait_for_task(submitted["task_id"])

        self.assertEqual(result["status"], "FAILED")
        self.assertFalse(result["acceptance"]["verified"])
        self.assertEqual(
            self.request(f"/api/tasks/{submitted['task_id']}/diff")[0], 404
        )
        self.assertEqual(
            self.request(
                f"/api/tasks/{submitted['task_id']}/promote",
                "POST",
                {"approved": True},
            )[0],
            404,
        )
        self.assertIn(
            "factorial", (self.workspace / "factorial.py").read_text(encoding="utf-8")
        )

    def test_old_success_without_task_acceptance_cannot_be_promoted(self):
        key = self.seed_review()
        self.service._tasks[key]["acceptance"]["verified"] = False
        self.assertEqual(self.request(f"/api/tasks/{key}/diff")[0], 404)
        self.assertEqual(
            self.request(f"/api/tasks/{key}/promote", "POST", {"approved": True})[0],
            404,
        )


if __name__ == "__main__":
    unittest.main()
