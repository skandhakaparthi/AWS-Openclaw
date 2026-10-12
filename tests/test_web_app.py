"""Local dashboard API tests with temporary workspaces and no Docker dependency."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent.task_workspace import TaskWorkspaceManager
from llm.client import LLMClient
from web.app import DemoService, create_server


class FakeState:
    def __init__(self, execution, classification):
        self.state = {"status": "FAILED", "iterations": 1, "plan": ["Run check"],
                      "files_changed": [], "latest_execution_result": execution,
                      "latest_test_result": {"verified": False},
                      "error_classification": classification}


class FakeAgent:
    def __init__(self, execution, classification):
        self.task_state = FakeState(execution, classification)
        self.history = [{"phase": "OBSERVE", "result": execution}]

    def run(self, task, filename):
        return {"status": "FAILED"}


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codepilot-web-test-")
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "demo.py").write_text("print('before')\n", encoding="utf-8")
        (self.root / "outside.py").write_text("secret\n", encoding="utf-8")
        self.service = DemoService(self.workspace, self.root / "artifacts", agent_factory=lambda: None)
        self.server = create_server("127.0.0.1", 0, self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path, method="GET", payload=None, token=True, origin=True):
        data = None if payload is None else json.dumps(payload).encode()
        headers = {}
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
                return response.status, json.loads(raw) if "json" in content_type else raw.decode()
        except HTTPError as exc:
            raw = exc.read()
            status = exc.code
            exc.close()
            return status, json.loads(raw) if raw else None

    def seed_review(self):
        manager = TaskWorkspaceManager(self.workspace, self.root / "artifacts")
        task_workspace = manager.create("task-webtest")
        (task_workspace / "demo.py").write_text("print('after')\n", encoding="utf-8")
        artifact = manager.create_review_artifact("task-webtest", ["demo.py"], "SUCCESS", {"verified": True})
        manager.cleanup()
        key = "a" * 32
        self.service._tasks[key] = {
            "task_id": key, "filename": "demo.py", "status": "SUCCESS", "created_at": time.time(),
            "iteration": 1, "plan": ["Run acceptance check"], "logs": [], "execution": {
                "success": True, "output": "ok", "error": "", "return_code": 0,
                "execution_mode": "docker-sandbox", "timed_out": False},
            "verification": {"verified": True, "return_code": 0}, "error_classification": "SUCCESS",
            "modified_files": ["demo.py"], "message": "Verified", "promotion_status": "NOT_REQUESTED",
            "manifest_path": artifact["manifest_path"], "agent": None,
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
        self.assertEqual(self.request("/api/session")[1]["csrf_token"], self.service.csrf_token)
        self.assertEqual(self.request("/api/workspace-files")[1]["files"], ["demo.py"])

    def test_submission_validation_and_unknown_task(self):
        for payload in ({"task": " ", "filename": "demo.py"},
                        {"task": "x" * 1501, "filename": "demo.py"},
                        {"task": "run", "filename": "../outside.py"},
                        {"task": "run", "filename": "outside.py"},
                        {"task": "run", "filename": "demo.py", "extra": True}):
            self.assertEqual(self.request("/api/tasks", "POST", payload)[0], 400)
        self.assertEqual(self.request("/api/tasks/" + "0" * 32)[0], 404)
        self.assertEqual(self.request("/api/tasks/../README.md")[0], 404)

    def test_write_requires_local_origin_and_csrf(self):
        payload = {"task": "run", "filename": "demo.py"}
        self.assertEqual(self.request("/api/tasks", "POST", payload, token=False)[0], 403)
        self.assertEqual(self.request("/api/tasks", "POST", payload, origin=False)[0], 403)
        self.assertEqual(self.request("/api/tasks", "POST", payload)[0], 202)

    def test_diff_rejection_and_explicit_approval(self):
        key = self.seed_review()
        status, diff = self.request(f"/api/tasks/{key}/diff")
        self.assertEqual(status, 200)
        self.assertIn("print('after')", diff["patch"])
        self.assertEqual(self.request(f"/api/tasks/{key}/promote", "POST", {"approved": False})[0], 403)
        self.assertIn("before", (self.workspace / "demo.py").read_text(encoding="utf-8"))
        self.assertEqual(self.request(f"/api/tasks/{key}/reject", "POST", {})[0], 200)
        self.assertIn("before", (self.workspace / "demo.py").read_text(encoding="utf-8"))

    def test_explicit_approval_promotes_reviewed_patch(self):
        key = self.seed_review()
        status, result = self.request(f"/api/tasks/{key}/promote", "POST", {"approved": True})
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "APPLIED")
        self.assertIn("after", (self.workspace / "demo.py").read_text(encoding="utf-8"))

    def test_rejects_non_loopback_bind(self):
        with self.assertRaises(ValueError):
            create_server("0.0.0.0", 0, self.service)

    def test_mock_mode_is_default_and_docker_unavailable_fails_closed(self):
        self.assertEqual(LLMClient(mode="mock").mode, "mock")
        default_service = DemoService(self.workspace, self.root / "default-artifacts")
        self.assertEqual(default_service.agent_factory().llm.mode, "mock")
        execution = {"success": False, "output": "", "error": "Docker unavailable", "return_code": -2,
                     "execution_mode": "docker-unavailable", "timed_out": False}
        self.service.agent_factory = lambda: FakeAgent(execution, "INFRA_ERROR")
        status, submitted = self.request("/api/tasks", "POST", {"task": "run", "filename": "demo.py"})
        self.assertEqual(status, 202)
        deadline = time.time() + 2
        snapshot = None
        while time.time() < deadline:
            _, snapshot = self.request(f"/api/tasks/{submitted['task_id']}")
            if snapshot["status"] != "RUNNING":
                break
            time.sleep(.01)
        self.assertEqual(snapshot["status"], "FAILED")
        self.assertIn("failed closed", snapshot["message"])
        self.assertFalse(snapshot["verification"]["verified"])

    def test_timeout_is_reported_without_success(self):
        execution = {"success": False, "output": "", "error": "timed out", "return_code": -1,
                     "execution_mode": "docker-sandbox", "timed_out": True}
        self.service.agent_factory = lambda: FakeAgent(execution, "TIMEOUT")
        _, submitted = self.request("/api/tasks", "POST", {"task": "run", "filename": "demo.py"})
        deadline = time.time() + 2
        snapshot = None
        while time.time() < deadline:
            _, snapshot = self.request(f"/api/tasks/{submitted['task_id']}")
            if snapshot["status"] != "RUNNING":
                break
            time.sleep(.01)
        self.assertEqual(snapshot["status"], "TIMED_OUT")
        self.assertFalse(snapshot["verification"]["verified"])


if __name__ == "__main__":
    unittest.main()
