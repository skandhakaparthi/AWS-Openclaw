"""Loopback-only web dashboard backed by the existing ReAct agent."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import http.server
import io
import ipaddress
import json
import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from agent.react_agent import PROJECT_ROOT, ReActAgent
from agent.task_workspace import TaskWorkspaceManager
from llm.client import LLMClient
from tools.executor import (
    docker_available,
    resolve_mode,
    run_python_file,
    sandbox_image_available,
)
from tools.filesystem import WORKSPACE

HOST = "127.0.0.1"
PORT = 8765
MAX_BODY_BYTES = 8_192
MAX_TASK_CHARS = 1_500
MAX_LOG_CHARS = 32_000
MAX_EVENT_CHARS = 4_000
MAX_TASKS = 20
TASK_KEY_RE = re.compile(r"\A[0-9a-f]{32}\Z")
FILENAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\.py\Z")
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/styles.css": ("styles.css", "text/css; charset=utf-8"),
}

FACTORIAL_FIXTURE = """def factorial(n):
    if n < 0:
        raise ValueError("factorial is undefined for negative integers")
    if n in (0, 1):
        return 1
    return n * factorial(n - 1)


def _run_task_checks():
    assert factorial(0) == 1, "factorial(0) must equal 1"
    assert factorial(1) == 1, "factorial(1) must equal 1"
    assert factorial(5) == 120, "factorial(5) must equal 120"
    print("FACTORIAL_FIXTURE_CHECKS_PASSED")


if __name__ == "__main__":
    _run_task_checks()
"""

ACCEPTANCE_PROFILES = {
    "factorial": {
        "id": "factorial-v1",
        "filename": "factorial.py",
        "criteria": [
            "factorial(0) == 1",
            "factorial(1) == 1",
            "factorial(5) == 120",
        ],
        "marker": "CODEPILOT_ACCEPTANCE:FACTORIAL:0=1,1=1,5=120",
    },
    "division_zero": {
        "id": "division-zero-v1",
        "filename": "repair_loop_temp.py",
        "criteria": [
            "divide(10, 0) returns a clear division-by-zero message",
            "divide(10, 2) returns 5",
        ],
        "marker": "CODEPILOT_ACCEPTANCE:DIVISION_ZERO:10/0=message,10/2=5",
    },
}


def _acceptance_profile(task, filename):
    factorial_requested = re.search(r"\bfactorial\b", task, re.IGNORECASE)
    division_requested = re.search(
        r"\b(divide|division)\b.*\bzero\b|\bzero\b.*\b(divide|division)\b",
        task,
        re.IGNORECASE,
    )
    if factorial_requested and division_requested:
        raise ValueError(
            "Choose one demo task at a time: factorial or division-by-zero."
        )
    profile_key = (
        "factorial"
        if factorial_requested
        else ("division_zero" if division_requested else None)
    )
    if profile_key is None:
        raise ValueError(
            "This local mock demo can verify factorial or division-by-zero tasks. "
            "Describe one of those tasks explicitly."
        )
    profile = ACCEPTANCE_PROFILES[profile_key]
    if filename != profile["filename"]:
        raise ValueError(
            f"This request requires {profile['filename']}; the selected file was {filename}. "
            "Choose the matching demo file and resubmit."
        )
    return {**profile, "kind": profile_key}


class _BoundedWriter(io.TextIOBase):
    """Discard console output after a fixed cap so agent prints cannot grow RAM."""

    def __init__(self, limit=MAX_LOG_CHARS):
        self.limit = limit
        self.parts = []
        self.length = 0

    def write(self, value):
        value = str(value)
        remaining = self.limit - self.length
        if remaining > 0:
            kept = value[:remaining]
            self.parts.append(kept)
            self.length += len(kept)
        return len(value)

    def flush(self):
        return None


def _short(value, limit=MAX_EVENT_CHARS):
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    return value[:limit]


def _execution_view(result):
    if not isinstance(result, dict):
        return None
    return {
        "success": result.get("success") is True,
        "output": _short(result.get("output", "")),
        "error": _short(result.get("error", "")),
        "return_code": result.get("return_code"),
        "execution_mode": _short(result.get("execution_mode", "unknown"), 80),
        "timed_out": result.get("timed_out") is True,
    }


def _history_view(history):
    result = []
    allowed = (
        "phase",
        "iteration",
        "decision",
        "action",
        "reason",
        "error",
        "subtasks",
    )
    for item in history[-80:]:
        if not isinstance(item, dict):
            continue
        event = {key: item[key] for key in allowed if key in item}
        if isinstance(item.get("result"), dict):
            event["result"] = _execution_view(item["result"])
        if isinstance(item.get("llm_analysis"), dict):
            event["analysis"] = {
                "action": _short(item["llm_analysis"].get("action"), 80),
                "reason": _short(item["llm_analysis"].get("reason")),
            }
        result.append(event)
    return result


def _local_origin(value, expected_port):
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost"}
            and parsed.port == expected_port
            and parsed.username is None
            and parsed.password is None
            and parsed.path == ""
            and parsed.query == ""
            and parsed.fragment == ""
        )
    except (TypeError, ValueError):
        return False


class DemoService:
    """Owns bounded in-memory task records and delegates work to ReActAgent."""

    def __init__(
        self,
        workspace=WORKSPACE,
        artifact_root=PROJECT_ROOT / ".agent_tasks",
        agent_factory=None,
    ):
        self.workspace = Path(workspace).resolve()
        self.artifact_root = Path(artifact_root).resolve()
        self.agent_factory = agent_factory or (
            lambda task_workspace, state_dir: ReActAgent(
                workspace=task_workspace,
                state_dir=state_dir,
                llm_client=LLMClient(mode="mock"),
            )
        )
        self.csrf_token = secrets.token_urlsafe(32)
        self._lock = threading.RLock()
        self._slots = threading.BoundedSemaphore(1)
        self._tasks = {}
        self._order = []

    def workspace_files(self):
        if not self.workspace.is_dir():
            return []
        files = []
        for path in self.workspace.glob("*.py"):
            try:
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or path.resolve().parent != self.workspace
                ):
                    continue
            except OSError:
                continue
            if FILENAME_RE.fullmatch(path.name):
                files.append(path.name)
        return sorted(files)

    def submit(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"task", "filename"}:
            raise ValueError("Send exactly a task and a filename.")
        task = payload.get("task")
        filename = payload.get("filename")
        if not isinstance(task, str) or not task.strip() or len(task) > MAX_TASK_CHARS:
            raise ValueError(
                f"Task text must contain 1 to {MAX_TASK_CHARS} characters."
            )
        if not isinstance(filename, str) or not FILENAME_RE.fullmatch(filename):
            raise ValueError("Choose a Python file from the demo workspace.")
        if filename not in self.workspace_files():
            raise ValueError("That file is not available in the demo workspace.")
        profile = _acceptance_profile(task, filename)
        if not self._slots.acquire(blocking=False):
            raise RuntimeError(
                "A task is already running. Wait for it to finish before starting another."
            )

        task_key = secrets.token_hex(16)
        record = {
            "task_id": task_key,
            "task": task,
            "filename": filename,
            "workspace": None,
            "acceptance": {
                "profile": profile["id"],
                "criteria": profile["criteria"],
                "verified": False,
            },
            "status": "RUNNING",
            "created_at": time.time(),
            "iteration": 0,
            "plan": [],
            "logs": [],
            "execution": None,
            "verification": {"verified": False},
            "error_classification": None,
            "modified_files": [],
            "message": "Starting the existing agent in mock mode.",
            "promotion_status": "NOT_REQUESTED",
            "manifest_path": None,
            "agent": None,
            "task_text": task,
            "profile": profile,
            "fixture_files": [],
        }
        with self._lock:
            self._tasks[task_key] = record
            self._order.append(task_key)
            self._prune()
        thread = threading.Thread(
            target=self._run_task,
            args=(task_key, task, filename),
            name="codepilot-demo-task",
            daemon=True,
        )
        try:
            thread.start()
        except RuntimeError:
            with self._lock:
                self._tasks[task_key].update(
                    {
                        "status": "FAILED",
                        "message": "The local task could not be started.",
                        "finished_at": time.time(),
                        "task_text": None,
                    }
                )
            self._slots.release()
            raise
        return {"task_id": task_key, "status": "RUNNING"}

    def _prune(self):
        while len(self._order) > MAX_TASKS:
            oldest = self._order[0]
            if self._tasks[oldest]["status"] in {"RUNNING", "STARTING"}:
                break
            self._order.pop(0)
            self._tasks.pop(oldest, None)

    def _run_task(self, task_key, task, filename):
        task_root_id = "task-" + task_key
        manager = TaskWorkspaceManager(self.workspace, self.artifact_root)
        try:
            task_workspace = manager.create(task_root_id)
            record = self._tasks[task_key]
            profile = record["profile"]
            record["workspace"] = str(task_workspace)
            if profile["kind"] == "factorial":
                (task_workspace / filename).write_text(
                    FACTORIAL_FIXTURE, encoding="utf-8"
                )
                record["fixture_files"] = [filename]

            state_dir = manager.task_root / "task_state"
            agent = self.agent_factory(task_workspace, state_dir)
            with self._lock:
                self._tasks[task_key]["agent"] = agent
            # The agent's console output is bounded and intentionally not returned;
            # structured events below come from the agent's existing history/state.
            captured = _BoundedWriter()
            with contextlib.redirect_stdout(captured):
                summary = agent.run(task, filename)
            state = getattr(getattr(agent, "task_state", None), "state", {}) or {}
            agent_execution = state.get("latest_execution_result")
            agent_check = state.get("latest_test_result")
            request_metadata = state.get("request_metadata") or {}
            request_digest = hashlib.sha256(
                task.encode("utf-8", errors="replace")
            ).hexdigest()
            executed_selected_file = any(
                isinstance(item, dict)
                and item.get("tool") == "run_python"
                and item.get("filename") == filename
                for item in state.get("tool_invocations", [])
            )
            request_matches = request_metadata.get("sha256") == request_digest
            agent_passed = (
                isinstance(summary, dict)
                and summary.get("status") == "SUCCESS"
                and state.get("status") == "SUCCESS"
                and request_matches
                and executed_selected_file
                and isinstance(agent_check, dict)
                and agent_check.get("verified") is True
                and isinstance(agent_execution, dict)
                and agent_execution.get("success") is True
                and agent_execution.get("return_code") == 0
                and agent_execution.get("execution_mode") == "docker-sandbox"
                and agent_execution.get("timed_out") is not True
            )
            classification = state.get("error_classification")
            acceptance_execution = None
            acceptance_verified = False
            if agent_passed:
                acceptance_execution = self._run_acceptance(
                    manager, task_workspace, filename, profile
                )
                acceptance_verified = (
                    isinstance(acceptance_execution, dict)
                    and acceptance_execution.get("success") is True
                    and acceptance_execution.get("return_code") == 0
                    and acceptance_execution.get("execution_mode") == "docker-sandbox"
                    and acceptance_execution.get("timed_out") is not True
                    and profile["marker"] in str(acceptance_execution.get("output", ""))
                )

            passed = agent_passed and acceptance_verified
            status = "SUCCESS" if passed else state.get("status", "FAILED")
            if not agent_passed and state.get("status") == "SUCCESS":
                status = "FAILED"
                classification = "REQUEST_STATE_MISMATCH"
            elif agent_passed and not acceptance_verified:
                status = "FAILED"
                classification = "ACCEPTANCE_FAILED"
            if not passed and (
                classification == "TIMEOUT"
                or (
                    isinstance(agent_execution, dict)
                    and agent_execution.get("timed_out") is True
                )
                or (
                    isinstance(acceptance_execution, dict)
                    and acceptance_execution.get("timed_out") is True
                )
            ):
                status = "TIMED_OUT"
            acceptance = {
                "profile": profile["id"],
                "criteria": profile["criteria"],
                "verified": acceptance_verified,
                "execution": _execution_view(acceptance_execution),
            }
            state["requested_task"] = task
            state["selected_file"] = filename
            state["task_acceptance"] = acceptance
            state["latest_execution_result"] = acceptance_execution or agent_execution
            state["latest_test_result"] = {"verified": passed, "acceptance": acceptance}

            manifest_path = None
            files = list(state.get("files_changed", []))
            if passed:
                files.extend(record.get("fixture_files", []))
                files = sorted(set(files))
                artifact = manager.create_review_artifact(
                    task_root_id,
                    files,
                    "SUCCESS",
                    {
                        "requested_task": task,
                        "selected_file": filename,
                        "dashboard_task_id": task_key,
                        "workspace": str(task_workspace),
                        "agent": agent_check,
                        "task_acceptance": acceptance,
                    },
                )
                manifest_path = artifact["manifest_path"]
                files = artifact["modified_files"]

            message = self._status_message(
                status, classification, acceptance_execution or agent_execution, passed
            )
            history = _history_view(getattr(agent, "history", []))
            logs_json = json.dumps(history, ensure_ascii=False)
            while len(logs_json) > MAX_LOG_CHARS and history:
                history.pop(0)
                logs_json = json.dumps(history, ensure_ascii=False)
            files = [
                name
                for name in files
                if isinstance(name, str) and FILENAME_RE.fullmatch(name)
            ][:40]
            with self._lock:
                record = self._tasks.get(task_key)
                if record is not None:
                    record.update(
                        {
                            "status": status,
                            "iteration": int(state.get("iterations", 0) or 0),
                            "plan": [
                                str(item)[:500] for item in state.get("plan", [])[:10]
                            ],
                            "logs": history,
                            "execution": _execution_view(
                                acceptance_execution or agent_execution
                            ),
                            "agent_execution": _execution_view(agent_execution),
                            "verification": {
                                "verified": passed,
                                "return_code": acceptance_execution.get("return_code")
                                if isinstance(acceptance_execution, dict)
                                else None,
                            },
                            "acceptance": acceptance,
                            "task_state_id": state.get("task_id"),
                            "error_classification": _short(classification, 100),
                            "modified_files": files,
                            "message": message,
                            "manifest_path": manifest_path,
                            "agent": None,
                            "task_text": None,
                            "finished_at": time.time(),
                        }
                    )
        except Exception:  # noqa: BLE001 - expose only a generic task failure to the browser.
            with self._lock:
                record = self._tasks.get(task_key)
                if record is not None:
                    record.update(
                        {
                            "status": "FAILED",
                            "message": "The agent stopped after an internal error.",
                            "error_classification": "INTERNAL_ERROR",
                            "agent": None,
                            "task_text": None,
                            "finished_at": time.time(),
                        }
                    )
        finally:
            cleanup = manager.cleanup()
            if not cleanup.get("success"):
                with self._lock:
                    record = self._tasks.get(task_key)
                    if record is not None:
                        record.update(
                            {
                                "status": "FAILED",
                                "message": "Task workspace cleanup failed; patch promotion is disabled.",
                                "acceptance": {
                                    **record.get("acceptance", {}),
                                    "verified": False,
                                },
                                "verification": {"verified": False},
                                "manifest_path": None,
                                "error_classification": "WORKSPACE_CLEANUP_FAILED",
                            }
                        )
            self._slots.release()

    @staticmethod
    def _run_acceptance(manager, task_workspace, filename, profile):
        verify_dir = manager.task_root / "acceptance"
        verify_dir.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(task_workspace / filename, verify_dir / filename)
        if profile["kind"] == "factorial":
            verifier_name = "verify_factorial.py"
            verifier = """from factorial import factorial
assert factorial(0) == 1, "factorial(0) must be 1"
assert factorial(1) == 1, "factorial(1) must be 1"
assert factorial(5) == 120, "factorial(5) must be 120"
print("CODEPILOT_ACCEPTANCE:FACTORIAL:0=1,1=1,5=120")
"""
        else:
            verifier_name = "verify_division_zero.py"
            verifier = """from repair_loop_temp import divide
zero_result = divide(10, 0)
assert isinstance(zero_result, str) and "zero" in zero_result.lower(), "division by zero must return a clear message"
assert divide(10, 2) == 5, "normal division must remain correct"
print("CODEPILOT_ACCEPTANCE:DIVISION_ZERO:10/0=message,10/2=5")
"""
        (verify_dir / verifier_name).write_text(verifier, encoding="utf-8")
        return run_python_file(verifier_name, workspace=verify_dir)

    @staticmethod
    def _status_message(status, classification, execution, passed):
        if passed:
            return "Acceptance check passed in the Docker sandbox. Review the patch before approving promotion."
        if status == "TIMED_OUT":
            return "Execution timed out. The agent stopped within its bounded runtime; no patch was promoted."
        if isinstance(execution, dict) and execution.get("execution_mode") in {
            "docker-unavailable",
            "image-missing",
            "docker-error",
        }:
            return "Docker sandbox unavailable. Execution failed closed; no local fallback was used."
        if classification == "PROVIDER_ERROR":
            return (
                "The configured model provider stopped the task. No patch was promoted."
            )
        if status in {"ROLLED_BACK", "FAILED"}:
            return "Acceptance check did not pass. The task workspace was discarded without promotion."
        return "The task ended without a verified success; no patch was promoted."

    def task_snapshot(self, task_key):
        if not TASK_KEY_RE.fullmatch(task_key):
            return None
        with self._lock:
            record = self._tasks.get(task_key)
            if record is None:
                return None
            agent = record.get("agent")
            view = {
                key: value
                for key, value in record.items()
                if key not in {"agent", "manifest_path", "task_text"}
            }
        if agent is not None:
            state = getattr(getattr(agent, "task_state", None), "state", {}) or {}
            view["iteration"] = int(state.get("iterations", 0) or 0)
            view["plan"] = [str(item)[:500] for item in state.get("plan", [])[:10]]
            view["error_classification"] = _short(
                state.get("error_classification"), 100
            )
            view["execution"] = _execution_view(state.get("latest_execution_result"))
            view["verification"] = {"verified": False}
            view["logs"] = _history_view(getattr(agent, "history", []))
        return view

    def diff(self, task_key):
        with self._lock:
            record = (
                self._tasks.get(task_key) if TASK_KEY_RE.fullmatch(task_key) else None
            )
            manifest_path = record.get("manifest_path") if record else None
            files = list(record.get("modified_files", [])) if record else []
            status = record.get("status") if record else None
        if status != "SUCCESS" or not manifest_path:
            return None
        with self._lock:
            accepted = (
                self._tasks.get(task_key, {}).get("acceptance", {}).get("verified")
                is True
            )
        if not accepted:
            return None
        try:
            manifest_file = Path(manifest_path).resolve(strict=True)
            manifest_file.relative_to(self.artifact_root)
            artifact_dir = manifest_file.parent
            patch_file = artifact_dir / "changes.patch"
            if (
                patch_file.is_symlink()
                or not patch_file.is_file()
                or patch_file.stat().st_size > MAX_LOG_CHARS
            ):
                return None
            patch = patch_file.read_text(encoding="utf-8")
        except (OSError, ValueError, RuntimeError, UnicodeError):
            return None
        return {"files": files, "patch": patch}

    def promote(self, task_key):
        with self._lock:
            record = (
                self._tasks.get(task_key) if TASK_KEY_RE.fullmatch(task_key) else None
            )
            if (
                record is None
                or record.get("status") != "SUCCESS"
                or not record.get("manifest_path")
                or record.get("acceptance", {}).get("verified") is not True
            ):
                return None
            if record.get("promotion_status") == "PROMOTING":
                raise RuntimeError("Patch promotion is already in progress.")
            record["promotion_status"] = "PROMOTING"
            manifest_path = record["manifest_path"]
        manager = TaskWorkspaceManager(self.workspace, self.artifact_root)
        try:
            result = manager.apply_reviewed_artifact(manifest_path, approved=True)
            with self._lock:
                record = self._tasks[task_key]
                record["promotion_status"] = result.get("status", "FAILED")
                record["promotion_result"] = {
                    key: result.get(key)
                    for key in (
                        "applied",
                        "status",
                        "conflicts",
                        "error",
                        "recovered_files",
                        "recovery_errors",
                    )
                    if key in result
                }
            return record["promotion_result"]
        except Exception:  # noqa: BLE001 - convert promotion errors to a bounded local status.
            with self._lock:
                self._tasks[task_key]["promotion_status"] = "FAILED"
                self._tasks[task_key]["promotion_result"] = {
                    "applied": False,
                    "status": "FAILED",
                    "recovery_errors": [],
                    "message": "Promotion failed; inspect the local artifact receipt.",
                }
            return self._tasks[task_key]["promotion_result"]

    def reject(self, task_key):
        with self._lock:
            record = (
                self._tasks.get(task_key) if TASK_KEY_RE.fullmatch(task_key) else None
            )
            if (
                record is None
                or record.get("status") != "SUCCESS"
                or not record.get("manifest_path")
                or record.get("acceptance", {}).get("verified") is not True
            ):
                return False
            if record.get("promotion_status") == "PROMOTING":
                return False
            record["promotion_status"] = "REJECTED"
            return True


class _RequestHandler(http.server.BaseHTTPRequestHandler):
    server_version = "CodePilotDemo/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def service(self):
        return self.server.demo_service

    def log_message(self, format, *args):
        # Avoid writing user task text, request bodies, or headers to server logs.
        return

    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        data = (
            body
            if isinstance(body, bytes)
            else (
                body.encode("utf-8")
                if isinstance(body, str)
                else json.dumps(body, ensure_ascii=False).encode("utf-8")
            )
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        self.end_headers()
        self.wfile.write(data)

    def _json_error(self, status, message):
        self._send(status, {"error": message})

    def _guard(self, write=False):
        try:
            address = ipaddress.ip_address(self.client_address[0])
            host = urlsplit("//" + self.headers.get("Host", "")).hostname
            host_port = urlsplit("//" + self.headers.get("Host", "")).port
        except (ValueError, TypeError):
            return False
        if (
            not address.is_loopback
            or host not in {"127.0.0.1", "localhost"}
            or host_port != self.server.server_port
        ):
            return False
        if write:
            if not _local_origin(self.headers.get("Origin"), self.server.server_port):
                return False
            fetch_site = self.headers.get("Sec-Fetch-Site")
            if fetch_site and fetch_site not in {"same-origin", "none"}:
                return False
            supplied = self.headers.get("X-CodePilot-CSRF", "")
            if not hmac.compare_digest(supplied, self.service.csrf_token):
                return False
        return True

    def _body(self):
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Content-Type must be application/json.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid request length.") from exc
        if not 1 <= length <= MAX_BODY_BYTES:
            raise ValueError(
                f"Request body must be between 1 and {MAX_BODY_BYTES} bytes."
            )
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be valid JSON.") from exc

    def do_GET(self):
        if not self._guard():
            return self._json_error(403, "Local browser access required.")
        path = urlsplit(self.path).path
        if path in STATIC_FILES:
            filename, content_type = STATIC_FILES[path]
            file_path = Path(__file__).parent / "static" / filename
            try:
                data = file_path.read_bytes()
            except OSError:
                return self._json_error(500, "Dashboard asset unavailable.")
            if path == "/":
                data = data.replace(
                    b"__CODEPILOT_CSRF_TOKEN__", self.service.csrf_token.encode("ascii")
                )
            return self._send(200, data, content_type)
        if path == "/api/session":
            return self._send(200, {"csrf_token": self.service.csrf_token})
        if path == "/api/health":
            available = docker_available()
            image = sandbox_image_available() if available else False
            return self._send(
                200,
                {
                    "ok": True,
                    "llm_mode": "mock",
                    "execution_mode": resolve_mode() or "rejected",
                    "docker_available": available,
                    "sandbox_image_available": image,
                },
            )
        if path == "/api/workspace-files":
            return self._send(200, {"files": self.service.workspace_files()})
        match = re.fullmatch(r"/api/tasks/([0-9a-f]{32})(/diff)?", path)
        if match:
            task_key, diff_suffix = match.groups()
            if diff_suffix:
                result = self.service.diff(task_key)
                if result is None:
                    return self._json_error(
                        404, "No review patch is available for that task."
                    )
                return self._send(200, result)
            result = self.service.task_snapshot(task_key)
            if result is None:
                return self._json_error(404, "Task not found.")
            return self._send(200, result)
        return self._json_error(404, "Not found.")

    def do_POST(self):
        if not self._guard(write=True):
            # Consume small rejected request bodies before responding. Otherwise
            # Windows may reset the TCP connection while unread bytes remain,
            # hiding the intended 403 response from the local browser/client.
            try:
                rejected_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                rejected_length = MAX_BODY_BYTES + 1
            if 0 <= rejected_length <= MAX_BODY_BYTES:
                self.rfile.read(rejected_length)
            else:
                self.close_connection = True
            return self._json_error(
                403, "Request origin or local approval token was rejected."
            )
        path = urlsplit(self.path).path
        try:
            payload = self._body()
        except ValueError as exc:
            return self._json_error(400, str(exc))
        if path == "/api/tasks":
            try:
                result = self.service.submit(payload)
            except ValueError as exc:
                return self._json_error(400, str(exc))
            except RuntimeError as exc:
                return self._json_error(429, str(exc))
            return self._send(202, result)
        match = re.fullmatch(r"/api/tasks/([0-9a-f]{32})/(promote|reject)", path)
        if match:
            task_key, action = match.groups()
            if action == "promote":
                if (
                    not isinstance(payload, dict)
                    or set(payload) != {"approved"}
                    or payload.get("approved") is not True
                ):
                    return self._json_error(
                        403, "Explicit approval is required for promotion."
                    )
                result = self.service.promote(task_key)
                if result is None:
                    return self._json_error(
                        404, "No verified patch is available to promote."
                    )
                return self._send(200, result)
            if payload != {}:
                return self._json_error(
                    400, "Reject requests must use an empty JSON object."
                )
            if not self.service.reject(task_key):
                return self._json_error(
                    404, "No verified patch is available to reject."
                )
            return self._send(200, {"status": "REJECTED"})
        return self._json_error(404, "Not found.")


def create_server(host=HOST, port=PORT, service=None):
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("The demo server may only bind to loopback.")
    server = http.server.ThreadingHTTPServer((host, port), _RequestHandler)
    server.daemon_threads = True
    server.demo_service = service or DemoService()
    return server


def main():
    server = create_server()
    print(f"CodePilot-AI demo is in mock mode at http://127.0.0.1:{server.server_port}")
    print("Execution remains Docker-only and fail-closed. Press Ctrl+C to stop.")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
