import contextlib
import inspect
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools import executor
from tools.permissions import ToolPermissionManager
from tools.tool_registry import ToolRegistry

DOCKERFILE = Path(__file__).resolve().parent.parent / "sandbox" / "Dockerfile"


def ok_result(returncode=0, stdout="", stderr=""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


@contextlib.contextmanager
def execution_mode(value):
    """Set CODEPILOT_EXECUTION_MODE for a block (None = unset)."""
    with patch.dict(os.environ):
        if value is None:
            os.environ.pop("CODEPILOT_EXECUTION_MODE", None)
        else:
            os.environ["CODEPILOT_EXECUTION_MODE"] = value
        yield


class TestFailClosedModes(unittest.TestCase):
    """Sandbox-required mode must never fall back to local execution."""

    def test_docker_missing_fails_closed_without_executing(self):
        with execution_mode("docker"), \
                patch.object(executor.shutil, "which", return_value=None), \
                patch.object(executor.subprocess, "run") as run:
            result = executor.run_python_file("factorial.py")

        self.assertFalse(result["success"])
        self.assertEqual(result["return_code"], -2)
        self.assertEqual(result["execution_mode"], "docker-unavailable")
        self.assertIn("No code was executed", result["error"])
        run.assert_not_called()

    def test_daemon_failure_never_runs_local_python(self):
        commands = []

        def failing_run(command, **kwargs):
            commands.append(list(command))
            raise OSError("daemon down")

        with execution_mode("docker"), \
                patch.object(executor.shutil, "which",
                             return_value="C:/docker/docker.exe"), \
                patch.object(executor.subprocess, "run",
                             side_effect=failing_run):
            result = executor.run_python_file("factorial.py")

        self.assertFalse(result["success"])
        self.assertEqual(result["execution_mode"], "docker-unavailable")
        # The only attempted command is `docker info` - never a host python.
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][1], "info")
        self.assertNotIn("python", commands[0][0])

    def test_default_mode_is_docker_when_env_unset(self):
        with execution_mode(None), \
                patch.object(executor.shutil, "which", return_value=None), \
                patch.object(executor.subprocess, "run") as run:
            result = executor.run_python_file("factorial.py")

        self.assertEqual(result["execution_mode"], "docker-unavailable")
        run.assert_not_called()

    def test_legacy_and_unknown_modes_fail_closed(self):
        for bad in ("auto", "local", "banana"):
            with self.subTest(mode=bad), execution_mode(bad), \
                    patch.object(executor.subprocess, "run") as run:
                result = executor.run_python_file("factorial.py")

            self.assertFalse(result["success"])
            self.assertEqual(result["return_code"], -2)
            self.assertEqual(result["execution_mode"], "mode-rejected")
            self.assertIn("No code was executed", result["error"])
            run.assert_not_called()

    def test_local_execution_requires_explicit_opt_in_and_is_labeled(self):
        with execution_mode("local-unsandboxed"), \
                patch.object(executor.subprocess, "run",
                             return_value=ok_result(0, stdout="hi\n")) as run:
            result = executor.run_python_file("factorial.py")

        self.assertTrue(result["success"])
        self.assertIn("UNSANDBOXED", result["execution_mode"])
        command = run.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        # No docker involved in local mode.
        self.assertFalse(
            any("docker" in str(call) for call in run.call_args_list)
        )

    def test_path_traversal_rejected_before_any_execution(self):
        for evil in ("../secrets.env", "..\\..\\x.py", "/etc/passwd",
                     "C:\\Windows\\win.ini", ""):
            with self.subTest(filename=evil), execution_mode("docker"), \
                    patch.object(executor, "docker_available",
                                 return_value=True), \
                    patch.object(executor, "sandbox_image_available",
                                 return_value=True), \
                    patch.object(executor.subprocess, "run") as run:
                result = executor.run_python_file(evil)

            self.assertFalse(result["success"])
            self.assertEqual(result["execution_mode"], "path-rejected")
            run.assert_not_called()


class TestDockerCommandHardening(unittest.TestCase):
    """The docker run command must carry every required restriction."""

    def _docker_call(self, filename="factorial.py", responder=None):
        """Run run_python_file in docker mode with mocked subprocess."""
        captured = []

        def fake_run(command, **kwargs):
            captured.append(list(command))
            if responder is not None:
                return responder(command, **kwargs)
            return ok_result(0, stdout="ok")

        with execution_mode("docker"), \
                patch.object(executor, "docker_available",
                             return_value=True), \
                patch.object(executor, "sandbox_image_available",
                             return_value=True), \
                patch.object(executor.subprocess, "run",
                             side_effect=fake_run):
            result = executor.run_python_file(filename)

        return result, captured

    @staticmethod
    def _run_command(commands):
        for command in commands:
            if command[:2] == ["docker", "run"]:
                return command
        raise AssertionError("docker run command was not issued")

    def test_network_is_disabled(self):
        _, commands = self._docker_call()
        command = self._run_command(commands)

        self.assertEqual(command[command.index("--network") + 1], "none")

    def test_cpu_memory_and_pid_limits_supplied(self):
        _, commands = self._docker_call()
        command = self._run_command(commands)

        self.assertEqual(command[command.index("--cpus") + 1], "1")
        self.assertEqual(command[command.index("--memory") + 1], "256m")
        self.assertEqual(command[command.index("--pids-limit") + 1], "64")

    def test_non_root_user_configured(self):
        _, commands = self._docker_call()
        command = self._run_command(commands)

        self.assertEqual(command[command.index("--user") + 1], "sandboxuser")
        dockerfile = DOCKERFILE.read_text(encoding="utf-8-sig")
        self.assertIn("USER sandboxuser", dockerfile)

    def test_read_only_rootfs_and_writable_tmp_only(self):
        _, commands = self._docker_call()
        command = self._run_command(commands)

        self.assertIn("--read-only", command)
        tmpfs = command[command.index("--tmpfs") + 1]
        self.assertTrue(tmpfs.startswith("/tmp:"))

    def test_capabilities_dropped_and_no_new_privileges(self):
        _, commands = self._docker_call()
        command = self._run_command(commands)

        self.assertEqual(command[command.index("--cap-drop") + 1], "ALL")
        opt = command[command.index("--security-opt") + 1]
        self.assertIn("no-new-privileges", opt)

    def test_timeout_handling_kills_and_cleans_container(self):
        def responder(command, **kwargs):
            if command[:2] == ["docker", "run"]:
                raise subprocess.TimeoutExpired(cmd=command, timeout=10)
            return ok_result(0)

        result, commands = self._docker_call(responder=responder)

        self.assertFalse(result["success"])
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["return_code"], -1)
        # react_agent.reflect() relies on this exact contract:
        self.assertIn("timed out", result["error"].lower())

        run_cmd = self._run_command(commands)
        self.assertIn("--rm", run_cmd)
        name = run_cmd[run_cmd.index("--name") + 1]
        cleanup = [c for c in commands if c[:3] == ["docker", "rm", "-f"]]
        self.assertEqual(len(cleanup), 1,
                         "timed-out container must be force-removed")
        self.assertEqual(cleanup[0][3], name)
        self.assertEqual(result["container_cleanup"]["success"], True)

    def test_timeout_cleanup_failure_is_reported(self):
        def responder(command, **kwargs):
            if command[:2] == ["docker", "run"]:
                raise subprocess.TimeoutExpired(cmd=command, timeout=10)
            return ok_result(1, stderr="daemon refused cleanup")

        result, _ = self._docker_call(responder=responder)
        self.assertFalse(result["container_cleanup"]["success"])
        self.assertEqual(result["container_cleanup"]["error"], "daemon refused cleanup")

    def test_cancellation_attempts_container_cleanup(self):
        calls = []

        def responder(command, **kwargs):
            calls.append(list(command))
            if command[:2] == ["docker", "run"]:
                raise KeyboardInterrupt()
            return ok_result(0)

        with execution_mode("docker"), \
                patch.object(executor, "docker_available", return_value=True), \
                patch.object(executor, "sandbox_image_available", return_value=True), \
                patch.object(executor.subprocess, "run", side_effect=responder):
            with self.assertRaises(KeyboardInterrupt):
                executor.run_python_file("factorial.py")
        self.assertTrue(any(command[:3] == ["docker", "rm", "-f"] for command in calls))

    def test_non_zero_exit_code_reported(self):
        def responder(command, **kwargs):
            if command[:2] == ["docker", "run"]:
                return ok_result(7, stderr="boom")
            return ok_result(0)

        result, _ = self._docker_call(responder=responder)

        self.assertFalse(result["success"])
        self.assertEqual(result["return_code"], 7)
        self.assertEqual(result["error"], "boom")
        self.assertEqual(result["execution_mode"], "docker-sandbox")
        self.assertFalse(result["timed_out"])

    def test_cleanup_after_run_failure(self):
        def responder(command, **kwargs):
            if command[:2] == ["docker", "run"]:
                raise OSError("cannot start container")
            return ok_result(0)

        result, commands = self._docker_call(responder=responder)

        self.assertFalse(result["success"])
        self.assertEqual(result["execution_mode"], "docker-error")
        self.assertEqual(result["return_code"], -2)

        run_cmd = self._run_command(commands)
        name = run_cmd[run_cmd.index("--name") + 1]
        cleanup = [c for c in commands if c[:3] == ["docker", "rm", "-f"]]
        self.assertEqual(len(cleanup), 1,
                         "failed container must be force-removed")
        self.assertEqual(cleanup[0][3], name)

    def test_single_read_only_workspace_mount_no_host_credentials(self):
        _, commands = self._docker_call()
        command = self._run_command(commands)

        volumes = [
            command[i + 1] for i, flag in enumerate(command)
            if flag in ("-v", "--volume", "--mount")
        ]
        expected = (
            str(executor.WORKSPACE).replace("\\", "/") + ":/workspace:ro"
        )
        self.assertEqual(volumes, [expected],
                         "exactly one mount: workspace read-only")

        joined = " ".join(command)
        self.assertNotIn("docker.sock", joined)
        self.assertNotIn(".aws", joined)
        self.assertNotIn(".ssh", joined)
        self.assertNotIn("--privileged", command)
        self.assertNotIn("-p", command)
        self.assertNotIn("--publish", command)

    def test_no_host_environment_passed(self):
        _, commands = self._docker_call()
        command = self._run_command(commands)

        env_flags = [
            command[i + 1] for i, flag in enumerate(command)
            if flag in ("-e", "--env")
        ]
        self.assertEqual(env_flags, [],
                         "no host environment (e.g. AWS creds) may be passed")


class TestToolRegistryCannotBypassSandbox(unittest.TestCase):
    """Execution is env-controlled at a single chokepoint."""

    def test_registry_execution_routes_through_executor_chokepoint(self):
        with patch("tools.tool_registry.run_python_file") as run_file:
            run_file.return_value = {"success": True}
            result = ToolRegistry().run_python("factorial.py")

        run_file.assert_called_once_with("factorial.py")
        self.assertEqual(result, {"success": True})

    def test_registry_offers_no_alternate_execution_tool(self):
        self.assertEqual(
            ToolPermissionManager.ALLOWED_TOOLS,
            {"read_file", "write_file", "run_python"},
        )
        registry = ToolRegistry()
        with self.assertRaises(PermissionError):
            registry.permissions.check("shell_exec")

    def test_registry_cannot_select_execution_mode(self):
        # run_python only forwards a filename; the mode is env-controlled.
        signature = inspect.signature(ToolRegistry.run_python)
        self.assertEqual(list(signature.parameters), ["self", "filename"])


if __name__ == "__main__":
    unittest.main(verbosity=2)



