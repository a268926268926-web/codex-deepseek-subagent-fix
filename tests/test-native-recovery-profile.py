#!/usr/bin/env python3
"""Hermetic tests for the --native-profile pin in recover-v2-tasks.py.

Nothing here calls a real model service. Command-line construction, child
environment and credential isolation are checked against the innermost
guarded_read_once boundary, and the profile loader is exercised with private
temporary files.
"""
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import textwrap
import unittest
from unittest import mock
from types import SimpleNamespace

sys.dont_write_bytecode = True

spec = importlib.util.spec_from_file_location("recovery", Path(__file__).resolve().parents[1] / "scripts" / "recover-v2-tasks.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)

SECRET = "sk-native-isolated-secret-000111"
SENTINEL = "gAAAAA-native-recovery-opaque"


def private_profile(directory, **overrides):
    document = {"version": 1, "base_url": "https://native.upstream.invalid/v1",
                "api_key": SECRET, "provider_name": "OpenAI"}
    document.update(overrides)
    path = Path(directory) / "native-upstream.local.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    path.chmod(0o600)
    return path


def task_item():
    return {"type": "agent_message", "id": "amsg-native", "content": [
            {"type": "input_text", "text": "Message Type: NEW_TASK\nTask name: /root/x\nSender: /root\nPayload:\n"},
            {"type": "encrypted_content", "encrypted_content": "local-cipher-native"}]}


class NativeProfileLoaderTests(unittest.TestCase):
    def test_valid_profile_returns_fields_and_default_provider_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = private_profile(temporary, base_url="https://native.upstream.invalid/v1/")
            profile = recovery.load_native_profile(path)
            self.assertEqual(profile, {"version": 1, "base_url": "https://native.upstream.invalid/v1",
                                       "api_key": SECRET, "provider_name": "OpenAI"})
            defaulted = private_profile(temporary, provider_name=None)
            document = json.loads(defaulted.read_text())
            document.pop("provider_name")
            defaulted.write_text(json.dumps(document), encoding="utf-8")
            defaulted.chmod(0o600)
            self.assertEqual(recovery.load_native_profile(defaulted)["provider_name"], "OpenAI")

    def test_group_or_other_readable_profile_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            for mode in (0o640, 0o604, 0o644, 0o666, 0o700):
                with self.subTest(mode=oct(mode)):
                    path = private_profile(temporary)
                    path.chmod(mode)
                    if stat.S_IMODE(path.stat().st_mode) != mode:
                        self.skipTest("Filesystem does not preserve the requested mode")
                    if mode & 0o077:
                        with self.assertRaisesRegex(ValueError, "group or other"):
                            recovery.load_native_profile(path)
                    else:
                        self.assertTrue(recovery.load_native_profile(path)["api_key"])

    def test_insecure_documents_are_rejected_without_echoing_the_secret(self):
        cases = [
            ("missing file", Path("/nonexistent/native.local.json"), None),
            ("http base_url", None, {"base_url": "http://native.invalid/v1"}),
            ("base_url userinfo", None, {"base_url": "https://user:pass@native.invalid/v1"}),
            ("base_url empty userinfo", None, {"base_url": "https://@native.invalid/v1"}),
            ("base_url empty password", None, {"base_url": "https://user:@native.invalid/v1"}),
            ("base_url non-numeric port", None, {"base_url": "https://native.invalid:notaport/v1"}),
            ("base_url out-of-range port", None, {"base_url": "https://native.invalid:70000/v1"}),
            ("base_url zero port", None, {"base_url": "https://native.invalid:0/v1"}),
            ("base_url unbracketed ipv6", None, {"base_url": "https://::1/v1"}),
            ("base_url query", None, {"base_url": "https://native.invalid/v1?key=1"}),
            ("base_url fragment", None, {"base_url": "https://native.invalid/v1#frag"}),
            ("base_url backslash", None, {"base_url": "https://native.invalid\\v1"}),
            ("wrong version", None, {"version": 2}),
            ("bool version", None, {"version": True}),
            ("empty api_key", None, {"api_key": ""}),
            ("non-string api_key", None, {"api_key": 1234}),
            ("api_key with newline", None, {"api_key": "sk-native\ninjected"}),
            ("unknown field", None, {"unexpected": "value"}),
            ("blank provider_name", None, {"provider_name": "  "})]
        with tempfile.TemporaryDirectory() as temporary:
            for label, path, overrides in cases:
                with self.subTest(case=label):
                    if path is None:
                        path = private_profile(temporary, **overrides)
                    with self.assertRaises(ValueError) as raised:
                        recovery.load_native_profile(path)
                    self.assertNotIn(SECRET, str(raised.exception))

    def test_valid_explicit_port_is_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = private_profile(temporary, base_url="https://native.upstream.invalid:8443/v1")
            self.assertEqual(recovery.load_native_profile(path)["base_url"],
                             "https://native.upstream.invalid:8443/v1")

    def test_unsupported_field_error_does_not_echo_the_field_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            for field in ("gAAAAA_secret_like_field", SECRET):
                with self.subTest(field=field[:12]):
                    path = private_profile(temporary, **{field: "value"})
                    with self.assertRaisesRegex(ValueError, "unsupported fields") as raised:
                        recovery.load_native_profile(path)
                    self.assertNotIn(field, str(raised.exception))
                    self.assertNotIn(SECRET, str(raised.exception))

    def test_symlinked_or_oversized_profile_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            real = private_profile(directory)
            link = directory / "link.local.json"
            link.symlink_to(real)
            with self.assertRaisesRegex(ValueError, "symlink"):
                recovery.load_native_profile(link)
            oversized = private_profile(directory)
            oversized.write_text(json.dumps({"padding": "x" * recovery.MAX_NATIVE_PROFILE_BYTES}), encoding="utf-8")
            oversized.chmod(0o600)
            with self.assertRaisesRegex(ValueError, r"1\.\." + str(recovery.MAX_NATIVE_PROFILE_BYTES)):
                recovery.load_native_profile(oversized)

    def test_non_utf8_and_non_object_documents_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            binary = directory / "binary.local.json"
            binary.write_bytes(b"\xff\xfe\x00")
            binary.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "UTF-8"):
                recovery.load_native_profile(binary)
            array = directory / "array.local.json"
            array.write_text("[]", encoding="utf-8")
            array.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "JSON object"):
                recovery.load_native_profile(array)

    def test_non_regular_or_empty_profile_is_rejected_promptly(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fifo = directory / "named-pipe.local.json"
            os.mkfifo(fifo, 0o600)
            with self.assertRaisesRegex(ValueError, "regular file"):
                recovery.load_native_profile(fifo)
            empty = directory / "empty.local.json"
            empty.write_text("", encoding="utf-8")
            empty.chmod(0o600)
            with self.assertRaisesRegex(ValueError, r"1\.\." + str(recovery.MAX_NATIVE_PROFILE_BYTES)):
                recovery.load_native_profile(empty)


class ProviderIsolationTests(unittest.TestCase):
    def test_pin_uses_independent_provider_and_keeps_secret_out_of_argv(self):
        config = {"model_provider": "custom",
                  "model_providers": {"custom": {"base_url": "https://selected.invalid/v1"}}}
        args = mock.Mock(native_profile=Path("/tmp/native.local.json"), catalog=None,
                         config=[], codex_original=Path("/usr/bin/codex"), timeout=5,
                         output=Path("/tmp/out.json"), model="gpt-6.1-sol", effort="low")
        captured = {}

        def fake_guarded(args_, tasks, config_, provider, port, native=None):
            captured.update(native=native, provider=provider, port=port, config=config_)
            return ["Recovered"]

        def fake_load(path):
            self.assertEqual(path, Path("/tmp/native.local.json"))
            return {"version": 1, "base_url": "https://native.upstream.invalid/v1",
                    "api_key": SECRET, "provider_name": "OpenAI"}

        with mock.patch.object(recovery, "effective_config", return_value=config), \
                mock.patch.object(recovery, "load_native_profile", side_effect=fake_load), \
                mock.patch.object(recovery, "guarded_read_once", side_effect=fake_guarded), \
                mock.patch.object(recovery, "ThreadingHTTPServer") as server_class:
            server = server_class.return_value
            server.server_port = 43123
            self.assertEqual(recovery.read_once(args, [("x", task_item())]), ["Recovered"])
        self.assertEqual(server.upstream, "https://native.upstream.invalid/v1")
        self.assertEqual(server.model_routes, {})
        self.assertTrue(server.recovery_guard)
        self.assertEqual(captured["provider"], recovery.NATIVE_RECOVERY_PROVIDER_ID)
        self.assertEqual(captured["native"]["api_key"], SECRET)

    def test_loopback_selected_provider_is_rejected_without_a_pin(self):
        config = {"model_provider": "ccs",
                  "model_providers": {"ccs": {"base_url": "http://127.0.0.1:8080/v1"}}}
        args = SimpleNamespace(native_profile=None, config=[], catalog=None, timeout=5)
        with mock.patch.object(recovery, "effective_config", return_value=config), \
                mock.patch.object(recovery, "ThreadingHTTPServer") as server_class:
            with self.assertRaisesRegex(ValueError, "loopback placeholder"):
                recovery.read_once(args, [("x", task_item())])
            server_class.assert_not_called()

    def test_selected_https_provider_is_still_used_without_a_pin(self):
        config = {"model_provider": "custom",
                  "model_providers": {"custom": {"base_url": "https://selected.invalid/v1"}}}
        args = SimpleNamespace(native_profile=None, timeout=5)
        captured = {}

        def fake_guarded(args_, tasks, config_, provider, port, native=None):
            captured.update(provider=provider, native=native)
            return ["Recovered"]

        with mock.patch.object(recovery, "effective_config", return_value=config), \
                mock.patch.object(recovery, "guarded_read_once", side_effect=fake_guarded), \
                mock.patch.object(recovery, "ThreadingHTTPServer") as server_class:
            server_class.return_value.server_port = 1
            result = recovery.read_once(args, [("x", task_item())])
        self.assertEqual(result, ["Recovered"])
        self.assertEqual(captured["provider"], "custom")
        self.assertIsNone(captured["native"])


class CommandAndEnvironmentTests(unittest.TestCase):
    def test_pinned_command_uses_isolated_provider_and_secret_only_in_environment(self):
        profile = {"version": 1, "base_url": "https://native.upstream.invalid/v1",
                   "api_key": SECRET, "provider_name": "OpenAI"}
        args = mock.Mock(catalog=None, config=[], codex_original=Path("/usr/bin/codex"),
                         timeout=5, output=Path("/tmp/out.json"))
        recorded = {}

        def fake_popen(command, **kwargs):
            recorded["command"] = list(command)
            recorded["env"] = dict(kwargs["env"])
            raise KeyboardInterrupt

        with mock.patch.object(recovery.subprocess, "Popen", side_effect=fake_popen), \
                mock.patch.object(recovery.tempfile, "TemporaryDirectory"):
            with self.assertRaises(KeyboardInterrupt):
                recovery.guarded_read_once(args, [("x", task_item())], {}, "custom", 45999, profile)
        command, env = recorded["command"], recorded["env"]
        for override in recovery.native_provider_overrides(profile, 45999):
            self.assertIn(override, command)
        self.assertIn('model_provider="codex_v2_native_recovery"', command)
        self.assertEqual(command[-1], "app-server")
        self.assertNotIn(SECRET, " ".join(command))
        self.assertEqual(env[recovery.NATIVE_RECOVERY_ENV_KEY], SECRET)
        self.assertNotIn("CODEX_V2_PLAINTEXT_MESSAGES", env)
        self.assertNotIn("CODEX_V2_RECOVERY_MAP", env)

    def test_unpinned_command_still_targets_the_selected_provider(self):
        args = mock.Mock(catalog=None, config=[], codex_original=Path("/usr/bin/codex"),
                         timeout=5, output=Path("/tmp/out.json"))
        recorded = {}

        def fake_popen(command, **kwargs):
            recorded["command"] = list(command)
            raise KeyboardInterrupt

        with mock.patch.object(recovery.subprocess, "Popen", side_effect=fake_popen), \
                mock.patch.object(recovery.tempfile, "TemporaryDirectory"):
            with self.assertRaises(KeyboardInterrupt):
                recovery.guarded_read_once(args, [("x", task_item())], {}, "custom", 45998)
        self.assertIn('model_providers.custom.base_url="http://127.0.0.1:45998"', recorded["command"])
        self.assertNotIn(recovery.NATIVE_RECOVERY_ENV_KEY, os.environ)

    def test_preexisting_recovery_env_key_is_replaced_or_removed(self):
        with mock.patch.dict(os.environ, {recovery.NATIVE_RECOVERY_ENV_KEY: "stale",
                                          "CODEX_V2_RECOVERY_MAP": "stale",
                                          "CODEX_V2_PLAINTEXT_MESSAGES": "1"}, clear=False):
            pinned = recovery.recovery_environment({"api_key": SECRET})
            self.assertEqual(pinned[recovery.NATIVE_RECOVERY_ENV_KEY], SECRET)
            self.assertNotIn("CODEX_V2_RECOVERY_MAP", pinned)
            self.assertNotIn("CODEX_V2_PLAINTEXT_MESSAGES", pinned)
            unpinned = recovery.recovery_environment(None)
            self.assertNotIn(recovery.NATIVE_RECOVERY_ENV_KEY, unpinned)

    def test_secret_is_redacted_from_error_text(self):
        profile = {"api_key": SECRET}
        message = f"upstream rejected credentials {SECRET} for request"
        self.assertNotIn(SECRET, recovery.redact_secret(message, profile))
        self.assertIn("[redacted]", recovery.redact_secret(message, profile))
        self.assertEqual(recovery.redact_secret(message, None), message)


STUB_CLI = textwrap.dedent('''\
    #!/usr/bin/env python3
    """Minimal app-server double: records argv and env, then answers the guard RPCs."""
    import json, os, sys
    transcript = os.environ["STUB_TRANSCRIPT"]
    argv_log = os.environ["STUB_ARGV"]
    with open(argv_log, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(sys.argv[1:]) + "\\n")
    api_key = os.environ.get("CODEX_V2_NATIVE_RECOVERY_KEY")
    with open(transcript, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"key_present": api_key is not None,
                                 "key_len": len(api_key or ""),
                                 "plaintext_flag": "CODEX_V2_PLAINTEXT_MESSAGES" in os.environ,
                                 "map_flag": "CODEX_V2_RECOVERY_MAP" in os.environ}) + "\\n")
    exit_code = 0
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except ValueError:
            continue
        method = request.get("method")
        identifier = request.get("id")
        if identifier is None:
            continue
        if method == "initialize":
            result = {"userAgent": "stub"}
        elif method == "thread/start":
            result = {"thread": {"id": "stub-thread"}}
        elif method == "thread/inject_items":
            result = {}
        elif method == "turn/start":
            result = {"turn": {"id": "stub-turn"}}
        else:
            result = {}
        sys.stdout.write(json.dumps({"id": identifier, "result": result}) + "\\n")
        sys.stdout.flush()
        if method == "turn/start":
            item = {"method": "item/completed",
                    "params": {"threadId": "stub-thread", "item": {"type": "agentMessage", "text": "{\\"status\\": \\"RECOVERED_TASK_TEXT\\", \\"messages\\": [{\\"payload\\": \\"Recovered task text\\"}]}"}}}
            done = {"method": "turn/completed",
                    "params": {"threadId": "stub-thread", "turn": {"id": "stub-turn", "status": "completed"}}}
            sys.stdout.write(json.dumps(item) + "\\n" + json.dumps(done) + "\\n")
            sys.stdout.flush()
        if method == "turn/start":
            break
    sys.exit(exit_code)
    ''')


class EndToEndPinTests(unittest.TestCase):
    def test_pinned_read_returns_both_matching_texts_with_isolated_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            cli = directory / "stub-codex"
            cli.write_text(STUB_CLI, encoding="utf-8")
            cli.chmod(0o700)
            profile_path = private_profile(directory)
            output = directory / "recovered.json"
            argv_log = directory / "argv.log"
            transcript = directory / "transcript.log"
            task = task_item()
            tasks = [(recovery.hashlib.sha256(task["content"][1]["encrypted_content"].encode()).hexdigest(), task)]
            with mock.patch.dict(os.environ, {"STUB_TRANSCRIPT": str(transcript),
                                              "STUB_ARGV": str(argv_log),
                                              "CODEX_V2_PLAINTEXT_MESSAGES": "1",
                                              "CODEX_V2_RECOVERY_MAP": "stale"}, clear=False):
                args = SimpleNamespace(native_profile=profile_path, catalog=None,
                                       codex_original=cli, config=[], model="gpt-6.1-sol",
                                       effort="low", output=output, timeout=20)

                def fake_effective(config_args):
                    return {"model_provider": "ccs",
                            "model_providers": {"ccs": {"base_url": "http://127.0.0.1:8080/v1"}}}

                with mock.patch.object(recovery, "effective_config", side_effect=fake_effective):
                    results = [recovery.read_once(args, tasks), recovery.read_once(args, tasks)]
            self.assertEqual(results[0], ["Recovered task text"])
            self.assertEqual(results[0], results[1])
            records = [json.loads(line) for line in transcript.read_text().splitlines()]
            self.assertEqual(len(records), 2)
            self.assertTrue(all(record["key_present"] and record["key_len"] == len(SECRET)
                                for record in records))
            self.assertTrue(all(not record["plaintext_flag"] and not record["map_flag"]
                                for record in records))
            argv_lines = [json.loads(line) for line in argv_log.read_text().splitlines()]
            self.assertEqual(len(argv_lines), 2)
            for argv in argv_lines:
                self.assertIn("model_provider=\"codex_v2_native_recovery\"", argv)
                self.assertIn('model_providers.codex_v2_native_recovery.wire_api="responses"', argv)
                self.assertIn("model_providers.codex_v2_native_recovery.requires_openai_auth=false", argv)
                self.assertNotIn('model_providers.ccs.base_url=', " ".join(argv))
                self.assertNotIn(SECRET, " ".join(argv))
                self.assertNotIn("http://127.0.0.1:8080", " ".join(argv))

    def test_unpinned_loopback_selection_fails_before_any_child_starts(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            cli = directory / "stub-codex"
            cli.write_text(STUB_CLI, encoding="utf-8")
            cli.chmod(0o700)
            args = SimpleNamespace(native_profile=None, catalog=None, codex_original=cli,
                                   config=[], model="gpt-6.1-sol", effort="low",
                                   output=directory / "out.json", timeout=5)
            with mock.patch.object(recovery, "effective_config",
                                   return_value={"model_provider": "ccs", "model_providers": {
                                       "ccs": {"base_url": "http://localhost:8080/v1"}}}):
                with self.assertRaisesRegex(ValueError, "native-profile"):
                    recovery.read_once(args, [("digest", task_item())])
            self.assertFalse((directory / "out.json").exists())


if __name__ == "__main__":
    unittest.main()
