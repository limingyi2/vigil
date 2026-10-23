"""Targeted discovery/auth/reconnect tests; never use the user's Codex credentials."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from codex_adapter import AdapterError, CodexAdapter, handle_http
from codex_runtime import CodexRuntime, find_cli


class RuntimeTests(unittest.TestCase):
    def test_official_status_uses_exit_code_and_discards_identity_output(self):
        for exitcode, auth, error in [(0, "logged_in", None), (1, "logged_out", "codex_login_required"),
                                       (2, "unknown", "codex_auth_check_failed")]:
            runtime = CodexRuntime("fixture.exe", home="isolated-home")
            results = [SimpleNamespace(returncode=0, stdout=b"codex-cli 0.162.0-alpha.17.2\n"),
                       SimpleNamespace(returncode=exitcode)]
            with patch("codex_runtime.subprocess.run", side_effect=results) as run, \
                    patch.object(Path, "read_text", side_effect=AssertionError("credential read")):
                info = runtime.inspect()
            self.assertEqual((info["auth_status"], info["error"]), (auth, error))
            self.assertEqual(info["version"], "0.162.0-alpha.17.2")
            args, kwargs = run.call_args
            self.assertEqual(args[0], ["fixture.exe", "login", "status"])
            self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["env"]["CODEX_HOME"], "isolated-home")
            self.assertEqual(set(info), {"installed", "version", "auth_status", "error"})

    def test_missing_invalid_version_and_timeout_are_distinct(self):
        with patch("codex_runtime.find_cli", return_value=None), patch("codex_runtime.subprocess.run") as run:
            self.assertEqual(CodexRuntime().inspect()["error"], "codex_cli_missing")
            run.assert_not_called()
        for result, code in [(SimpleNamespace(returncode=0, stdout=b"unexpected PRIVATE"), "codex_version_failed"),
                             (subprocess.TimeoutExpired("fixture", 6), "codex_detection_timeout"),
                             (OSError("PRIVATE local details"), "codex_cli_unavailable")]:
            with patch("codex_runtime.subprocess.run", side_effect=[result]):
                info = CodexRuntime("fixture.exe").inspect()
            self.assertEqual(info["error"], code)
            self.assertNotIn("PRIVATE", json.dumps(info))

    @unittest.skipUnless(os.name == "nt", "Windows discovery")
    def test_windows_fallback_finds_desktop_and_npm_native_exe_without_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            exe = base / "OpenAI/Codex/bin/build/codex.exe"
            exe.parent.mkdir(parents=True); exe.touch()
            with patch("codex_runtime.shutil.which", return_value=None), \
                    patch.dict(os.environ, {"LOCALAPPDATA": tmp, "APPDATA": str(base / "roaming")}):
                self.assertEqual(Path(find_cli()), exe)
                exe.unlink()
                npm = base / "roaming/npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/codex/codex.exe"
                npm.parent.mkdir(parents=True); npm.touch()
                self.assertEqual(Path(find_cli()), npm)


class AutoConnectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.center = CodexAdapter(self.temp.name, cli="fixture.exe", auto_reconnect=True)

    def tearDown(self):
        self.center.process = None
        self.center.login_process = None
        self.center.close()
        self.temp.cleanup()

    def ready(self):
        info = {"installed": True, "version": "0.162.0", "auth_status": "logged_in", "error": None}
        self.center.runtime.info = info
        self.center.runtime.inspect = Mock(return_value=info)

    def test_unauthenticated_or_missing_cli_never_starts_server(self):
        for reason in ("codex_cli_missing", "codex_login_required", "codex_auth_check_failed"):
            self.center.runtime.inspect = Mock(return_value={"error": reason})
            with patch.object(self.center, "_spawn") as spawn:
                with self.assertRaises(AdapterError) as err:
                    self.center.connect()
            self.assertEqual(err.exception.code, reason)
            spawn.assert_not_called()
            self.assertEqual(self.center.status()["last_error"], reason)
            self.assertIsNone(self.center.reconnect_worker)

    def test_logged_in_handshake_connects_without_creating_task(self):
        self.ready()
        proc = Mock()
        with patch.object(self.center, "_spawn", return_value=proc), \
                patch("codex_adapter.threading.Thread"), patch.object(self.center, "_write"), \
                patch.object(self.center, "_rpc", side_effect=[{}, {"config": {}}, {"data": []}]) as rpc:
            status = self.center.connect()
        self.assertEqual(status["connection_status"], "connected")
        self.assertEqual([c.args[0] for c in rpc.call_args_list], ["initialize", "config/read", "model/list"])
        self.assertEqual(self.center.tasks, {})

    def test_initial_connection_failure_is_visible_and_does_not_spawn_retry_worker(self):
        self.ready()
        with patch.object(self.center, "_spawn", side_effect=OSError("PRIVATE")):
            with self.assertRaises(AdapterError):
                self.center.connect()
        self.assertEqual(self.center.status()["last_error"], "connect_failed")
        self.assertIsNone(self.center.reconnect_worker)
        self.assertNotIn("PRIVATE", json.dumps(self.center.status()))

    def test_retry_has_three_attempts_and_preserves_cancellation_epoch(self):
        self.center.enabled = True
        epoch = self.center.reconnect_epoch
        real_time = __import__("time")
        sleep = Mock()
        isolated_time = SimpleNamespace(sleep=sleep, time=real_time.time,
                                        monotonic=real_time.monotonic)
        with patch("codex_adapter.time", isolated_time), \
                patch.object(self.center, "connect", side_effect=AdapterError("rpc_timeout")) as connect:
            self.center._reconnect(epoch)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2, 4])
        self.assertEqual(connect.call_count, 3)
        self.assertEqual(self.center.status()["last_error"], "reconnect_exhausted")
        self.assertEqual(self.center.status()["reconnect_attempts"], 3)
        self.center.disconnect()
        with patch.object(self.center.runtime, "inspect") as inspect:
            with self.assertRaises(AdapterError):
                self.center.connect(_reconnect_epoch=epoch)
            inspect.assert_not_called()

    def test_disconnect_during_probe_cannot_resurrect_connection(self):
        self.ready()
        info = self.center.runtime.info
        self.center.runtime.inspect.side_effect = lambda: (self.center.disconnect(), info)[1]
        with patch.object(self.center, "_spawn") as spawn:
            with self.assertRaises(AdapterError):
                self.center.connect()
            spawn.assert_not_called()
        self.assertFalse(self.center.enabled)

    def test_login_route_is_local_admin_only_and_uses_official_flow(self):
        for local, admin in [(False, True), (True, False)]:
            self.assertEqual(handle_http("POST", "/api/agent-center/login", is_local=local,
                                         is_admin=admin, adapter=self.center)[0], 403)
        self.ready()
        proc = Mock(); proc.poll.return_value = None
        with patch("codex_adapter.subprocess.Popen", return_value=proc) as spawn, \
                patch("codex_adapter.attach_job"), patch("codex_adapter.threading.Thread"):
            code, status = handle_http("POST", "/api/agent-center/login", is_local=True,
                                       is_admin=True, adapter=self.center)
        self.assertEqual(code, 200)
        self.assertEqual(status["runtime"]["auth_status"], "logging_in")
        self.assertEqual(spawn.call_args.args[0], ["fixture.exe", "login"])
        for stream in ("stdin", "stdout", "stderr"):
            self.assertEqual(spawn.call_args.kwargs[stream], subprocess.DEVNULL)

    def test_successful_login_rechecks_auth_then_connects_and_cancelled_login_does_not(self):
        proc = Mock(); proc.wait.return_value = 0; proc.poll.return_value = 0
        self.center.enabled = True
        epoch = self.center.reconnect_epoch
        with patch("codex_adapter.close_job"), patch.object(self.center, "connect") as connect:
            self.center._finish_login(proc, epoch)
            connect.assert_called_once_with(_reconnect_epoch=epoch)
            connect.reset_mock()
            self.center.disconnect()
            self.center._finish_login(proc, epoch)
            connect.assert_not_called()

    def test_active_login_cannot_be_invalidated_by_connect_and_disconnect_cancels_it(self):
        proc = Mock(); proc.poll.return_value = None
        self.center.login_process = proc
        self.center.runtime.info = {"auth_status": "logging_in"}
        epoch = self.center.reconnect_epoch
        with patch.object(self.center.runtime, "inspect") as inspect:
            self.assertEqual(self.center.connect()["runtime"]["auth_status"], "logging_in")
            inspect.assert_not_called()
        self.assertEqual(self.center.reconnect_epoch, epoch)
        with patch("codex_adapter.close_job"):
            self.center.disconnect()
        proc.terminate.assert_called_once()
        self.assertEqual(self.center.status()["last_error"], "explicit_disconnect")

    def test_login_failure_and_timeout_are_safe_and_do_not_connect(self):
        for result, expected in [(1, "codex_login_failed"),
                                 (subprocess.TimeoutExpired("login", 300), "codex_login_timeout")]:
            self.center.enabled = True
            proc = Mock(); proc.poll.return_value = 0
            proc.wait.side_effect = [result]
            with patch("codex_adapter.close_job"), patch.object(self.center, "connect") as connect:
                self.center._finish_login(proc, self.center.reconnect_epoch)
                connect.assert_not_called()
            self.assertEqual(self.center.status()["last_error"], expected)


if __name__ == "__main__":
    unittest.main()
