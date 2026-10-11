#!/usr/bin/env python3
"""Synthetic tests for ensure-ccs-proxy.py: no real CCS, network, or config."""
import importlib.util
import io
import os
from pathlib import Path
import socket
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = Path(__file__).resolve().parent
HELPER = HERE / "ensure-ccs-proxy.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("ensure_ccs_proxy", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def loopback_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeProcess:
    def __init__(self, pid=4242, exit_code=None):
        self.pid = pid
        self._exit_code = exit_code
        self.terminated = False
        self.killed = False
        self.waited = []

    def poll(self):
        return self._exit_code

    def terminate(self):
        self.terminated = True
        self._exit_code = -15

    def kill(self):
        self.killed = True
        self._exit_code = -9

    def wait(self, timeout=None):
        self.waited.append(timeout)
        return self._exit_code


class EnsureCcsReadyTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_helper()
        self.upstream = "http://127.0.0.1:%d/v1" % loopback_port()

    def _exe(self, tmpdir):
        path = Path(tmpdir) / "cc-switch"
        path.write_text("#!/bin/sh\nexit 0\n")
        os.chmod(path, 0o755)
        return path

    def test_ready_port_returns_without_popen(self):
        _, port = self.mod._loopback_port(self.upstream)
        with mock.patch.object(self.mod, "_port_open", return_value=True), \
                mock.patch.object(self.mod.subprocess, "Popen") as popen:
            result = self.mod.ensure_ccs_ready(self.upstream)
        popen.assert_not_called()
        self.assertEqual(result, {"ready": True, "started": False, "port": port,
                                  "pid": None, "owned": False, "owned_pid": None})

    def test_existing_closed_instance_is_refused_and_not_killed(self):
        with mock.patch.object(self.mod, "_port_open", return_value=False), \
                mock.patch.object(self.mod, "_cc_switch_pids", return_value=[777]), \
                mock.patch.object(self.mod.subprocess, "Popen") as popen, \
                mock.patch.object(self.mod.os, "kill") as kill:
            with self.assertRaises(self.mod.CcsReadyError) as raised:
                self.mod.ensure_ccs_ready(self.upstream)
        popen.assert_not_called()
        kill.assert_not_called()
        self.assertIn("already running", str(raised.exception))

    def test_cold_start_starts_once_and_keeps_running(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exe = self._exe(tmpdir)
            proc = FakeProcess()
            with mock.patch.object(self.mod, "_port_open", side_effect=[False, True]), \
                    mock.patch.object(self.mod, "_cc_switch_pids", return_value=[]), \
                    mock.patch.object(self.mod.subprocess, "Popen", return_value=proc) as popen:
                result = self.mod.ensure_ccs_ready(self.upstream, executable=exe, timeout=5)
            self.assertEqual(popen.call_count, 1)
            self.assertEqual(popen.call_args.args[0], [str(exe)])
            kwargs = popen.call_args.kwargs
            self.assertIs(kwargs["stdin"], self.mod.subprocess.DEVNULL)
            self.assertIs(kwargs["stdout"], self.mod.subprocess.DEVNULL)
            self.assertIs(kwargs["stderr"], self.mod.subprocess.DEVNULL)
            self.assertTrue(kwargs["start_new_session"])
            self.assertNotIn("shell", kwargs)
            self.assertFalse(proc.terminated)
            self.assertEqual(result["started"], True)
            self.assertEqual(result["owned_pid"], proc.pid)

    def test_timeout_kills_only_owned_process(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exe = self._exe(tmpdir)
            proc = FakeProcess(pid=555)
            with mock.patch.object(self.mod, "_port_open", return_value=False), \
                    mock.patch.object(self.mod, "_cc_switch_pids", return_value=[]), \
                    mock.patch.object(self.mod.subprocess, "Popen", return_value=proc), \
                    mock.patch.object(self.mod.time, "monotonic", side_effect=[0.0, 0.0, 99.0]), \
                    mock.patch.object(self.mod.time, "sleep") as sleep:
                with self.assertRaises(self.mod.CcsReadyError):
                    self.mod.ensure_ccs_ready(self.upstream, executable=exe, timeout=1)
            sleep.assert_called_once_with(0.1)
            self.assertTrue(proc.terminated)
            self.assertEqual(proc.waited, [3])

    def test_rejects_non_loopback_and_credentials_and_bad_ports(self):
        for upstream in ("https://127.0.0.1:7863/v1",
                         "http://example.com:7863/v1",
                         "http://user:secret@127.0.0.1:7863/v1",
                         "http://127.0.0.1:7863/v1?key=secret",
                         "http://127.0.0.1:7863/v1#frag",
                         "http://127.0.0.1:0/v1",
                         "http://127.0.0.1:70000/v1"):
            with self.subTest(upstream=upstream):
                with self.assertRaises(self.mod.CcsReadyError):
                    self.mod.ensure_ccs_ready(upstream)

    def test_check_never_starts(self):
        upstream = self.upstream
        with mock.patch.object(self.mod, "_port_open", return_value=True), \
                mock.patch.object(self.mod, "ensure_ccs_ready") as ensure:
            with redirect_stdout(io.StringIO()):
                code = self.mod.main(["--upstream", upstream, "--check"])
        self.assertEqual(code, 0)
        ensure.assert_not_called()
        with mock.patch.object(self.mod, "_port_open", return_value=False), \
                mock.patch.object(self.mod, "ensure_ccs_ready") as ensure:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = self.mod.main(["--upstream", upstream, "--check"])
        self.assertEqual(code, 1)
        ensure.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
