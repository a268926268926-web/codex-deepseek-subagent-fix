#!/usr/bin/env python3
import copy
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
import urllib.request

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("relay", SCRIPTS / "routed-v2-relay.py")
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)
helper_spec = importlib.util.spec_from_file_location("recovery", SCRIPTS / "recover-v2-tasks.py")
recovery_helper = importlib.util.module_from_spec(helper_spec)
helper_spec.loader.exec_module(recovery_helper)
launcher_spec = importlib.util.spec_from_file_location("launcher", SCRIPTS / "launch-routed-codex.py")
launcher = importlib.util.module_from_spec(launcher_spec)
launcher_spec.loader.exec_module(launcher)


class RuntimeCompatibilityTests(unittest.TestCase):
    def test_native_deepseek_is_a_default_plaintext_alias(self):
        self.assertIn(launcher.NATIVE_DEEPSEEK_ALIAS, launcher.DEFAULT_ALIASES)
        self.assertEqual(launcher.NATIVE_DEEPSEEK_ALIAS, "cn:deepseek-v4.1-flash")
        self.assertEqual(launcher.PORTABLE_PARENT_ALIASES, ("gpt-6.1-sol", "gpt-6-luna"))

    def test_portable_mode_adds_current_parent_aliases_only_by_default(self):
        routes = {"cn:deepseek-v4.1-flash": "target"}
        self.assertEqual(
            launcher.effective_aliases([], routes, False),
            ["gpt-5.6-sol", "cn:deepseek-v4.1-flash"],
        )
        self.assertEqual(
            launcher.effective_aliases([], routes, True),
            ["gpt-5.6-sol", "cn:deepseek-v4.1-flash", "gpt-6.1-sol", "gpt-6-luna"],
        )
        self.assertEqual(
            launcher.effective_aliases(["custom-parent"], routes, True),
            ["custom-parent", "cn:deepseek-v4.1-flash"],
        )

    def test_launch_issue_is_logged_privately_in_headless_session(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
                "os.environ", {"XDG_STATE_HOME": temporary}, clear=True), mock.patch.object(
                    launcher.subprocess, "run") as notify:
            message = "请先完全退出当前 Codex"
            launcher.report_launch_issue(message)
            path = Path(temporary) / launcher.LAUNCH_LOG_NAME
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            log = path.read_text()
            self.assertIn(message, log)
            self.assertNotIn("apiKey", log)
            notify.assert_not_called()

    def test_launch_issue_uses_notifier_in_live_gui_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            wayland_socket = Path(temporary) / "wayland-test"
            wayland_socket.touch()
            environment = {
                "XDG_STATE_HOME": temporary,
                "XDG_RUNTIME_DIR": temporary,
                "WAYLAND_DISPLAY": wayland_socket.name,
            }
            with mock.patch.dict("os.environ", environment, clear=True), mock.patch.object(
                    launcher.subprocess, "run") as notify:
                launcher.report_launch_issue("请先完全退出当前 Codex")
            notify.assert_called_once()
            command = Path(notify.call_args.args[0][0]).name
            self.assertIn(command, {"notify-send", "gdbus"})

    def test_previously_verified_and_updated_runtimes_are_accepted(self):
        for version in ("0.162.0-alpha.17.2", "0.163.0-alpha.10.2"):
            with self.subTest(version=version), mock.patch.object(
                    launcher.subprocess, "run",
                    return_value=SimpleNamespace(stdout=f"codex-cli {version}\n")):
                self.assertEqual(launcher.verified_runtime(Path("/unused/codex")), version)

    def test_unverified_runtime_reports_the_version_and_stays_blocked(self):
        with mock.patch.object(launcher.subprocess, "run",
                               return_value=SimpleNamespace(stdout="codex-cli 0.164.0-alpha.1\n")):
            with self.assertRaisesRegex(launcher.RuntimeVersionError, "0.164.0-alpha.1"):
                launcher.verified_runtime(Path("/unused/codex"))

    def test_unexpected_output_is_not_accepted_or_echoed(self):
        with mock.patch.object(launcher.subprocess, "run",
                               return_value=SimpleNamespace(stdout="unexpected output\nsensitive text")):
            with self.assertRaises(launcher.RuntimeVersionError) as caught:
                launcher.verified_runtime(Path("/unused/codex"))
            self.assertIn("unrecognized", str(caught.exception))
            self.assertNotIn("sensitive", str(caught.exception))

    def test_routes_config_can_enable_portable_plaintext(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "routes.json"
            path.write_text(json.dumps({
                "version": 1,
                "upstream": "http://127.0.0.1:15721/v1",
                "model_routes": {},
                "portable_plaintext": True,
            }))
            routes, profile, upstream, portable = launcher.load_routes_config(
                path,
                include_upstream=True,
                include_portable=True,
            )
            self.assertEqual(routes, {})
            self.assertIsNone(profile)
            self.assertEqual(upstream, "http://127.0.0.1:15721/v1")
            self.assertTrue(portable)

    def test_routes_config_rejects_non_boolean_portable_plaintext(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "routes.json"
            path.write_text(json.dumps({
                "version": 1,
                "model_routes": {},
                "portable_plaintext": "yes",
            }))
            with self.assertRaisesRegex(ValueError, "portable_plaintext"):
                launcher.load_routes_config(path, include_portable=True)


class CatalogContractTests(unittest.TestCase):
    def test_desktop_app_server_flags_share_scope_and_preserve_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            original = directory / "original cli"
            original.write_text("#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n")
            original.chmod(0o700)
            wrapper = directory / "wrapper.sh"
            wrapper.write_text(launcher.wrapper_text(original, "custom", "http://127.0.0.1:43541",
                                                      directory / "catalog with spaces.json"))
            wrapper.chmod(0o700)
            desktop = ["-c", "features.code_mode_host=true", "app-server", "--analytics-default-enabled",
                       "-c", "plugins.codex-app-tools@openai-bundled.mcp_servers.codex_app.enabled=true",
                       "-c", "plugins.code-review@openai-bundled.mcp_servers.code-review.enabled=true"]
            result = subprocess.run([str(wrapper), *desktop], capture_output=True, text=True, check=True)
            actual = json.loads(result.stdout)
            self.assertEqual(actual[:3], desktop[:3])
            self.assertEqual(actual[-len(desktop[3:]):], desktop[3:])
            inserted = actual[3:-len(desktop[3:])]
            self.assertIn('model_providers.custom.base_url="http://127.0.0.1:43541"', inserted)
            self.assertTrue(all(inserted[i] == "-c" for i in range(0, len(inserted), 2)))
            # CLI overrides must be inserted after the subcommand, not before it.
            normal = ["--no-daemon", "exec", "-c", "model=app-server", "-"]
            result = subprocess.run([str(wrapper), *normal], capture_output=True, text=True, check=True)
            actual = json.loads(result.stdout)
            self.assertEqual(actual[:3], normal[:3])
            self.assertIn('model_providers.custom.base_url="http://127.0.0.1:43541"', actual[3:-3])
            self.assertEqual(actual[-3:], normal[-3:])

    def test_routing_preserves_code_mode_and_native_model_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "source.json"
            for mode in ("code_mode", "code_mode_only"):
                with self.subTest(mode=mode):
                    original = {"models": [
                        {"slug": "gpt-5.6-sol", "tool_mode": mode,
                         "supports_search_tool": True, "use_responses_lite": True,
                         "multi_agent_version": "v2", "shell_type": "shell_command",
                         "base_instructions": "Mapped instructions"},
                        {"slug": "native-test", "tool_mode": "code_mode_only",
                         "supports_search_tool": True, "use_responses_lite": True,
                         "base_instructions": "Native instructions"}]}
                    source.write_text(json.dumps(original))
                    output = directory / f"{mode}.json"
                    subprocess.run([sys.executable, str(SCRIPTS / "make-routed-catalog.py"),
                                    "--source", str(source), "--output", str(output)],
                                   capture_output=True, text=True, check=True)
                    expected = copy.deepcopy(original)
                    expected["models"][0].update(supports_search_tool=False, use_responses_lite=False,
                                                  multi_agent_version="v2")
                    self.assertEqual(json.loads(output.read_text()), expected)
                    self.assertEqual(json.loads(source.read_text()), original)

    def test_native_deepseek_alias_replaces_luna_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            catalog = directory / "models.json"
            catalog.write_text(json.dumps({"models": [
                {"slug": "gpt-6.1-sol"},
                {"slug": "gpt-5.6-luna", "display_name": "Luna",
                 "tool_mode": "code_mode_only", "multi_agent_version": "v2"},
            ]}))
            command = [sys.executable, str(SCRIPTS / "add-native-deepseek-alias.py"),
                       "--catalog", str(catalog)]
            subprocess.run(command, check=True, capture_output=True, text=True)
            converted = json.loads(catalog.read_text())
            slugs = [model["slug"] for model in converted["models"]]
            self.assertIn("cn:deepseek-v4.1-flash", slugs)
            self.assertNotIn("gpt-5.6-luna", slugs)
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            self.assertIn('"changed": false', result.stdout)

    def test_launcher_accepts_code_mode_and_rejects_missing_exec_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            original = directory / "codex"
            app = directory / "app"
            for path in (original, app):
                path.write_text("test executable")
                path.chmod(0o700)
            (directory / "config.toml").write_text(
                'model_provider = "custom"\n[model_providers.custom]\n'
                'base_url = "https://example.invalid/v1"\nwire_api = "responses"\n')
            args = SimpleNamespace(codex_original=original, app=app, catalog=directory / "models.json",
                                   alias=["gpt-5.6-sol"], recovery_map=directory / "missing-map.json")
            version = SimpleNamespace(stdout=f"codex-cli {launcher.VERSION}\n")
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(directory)}), \
                    mock.patch.object(launcher.subprocess, "run", return_value=version):
                for mode in ("code_mode", "code_mode_only"):
                    args.catalog.write_text(json.dumps({"models": [
                        {"slug": "gpt-5.6-sol", "tool_mode": mode, "multi_agent_version": "v2"}]}))
                    self.assertEqual(launcher.validate(args), ("custom", "https://example.invalid/v1", 0))
                args.catalog.write_text(json.dumps({"models": [
                    {"slug": "gpt-5.6-sol", "tool_mode": "direct", "multi_agent_version": "v2"}]}))
                with self.assertRaisesRegex(ValueError, "Code Mode"):
                    launcher.validate(args)
            self.assertIn("features.code_mode_host=true",
                          launcher.wrapper_text(original, "custom", "http://127.0.0.1:1", args.catalog))


class ContractTests(unittest.TestCase):
    def test_task_and_queue_communication_are_in_scope(self):
        for kind in ("NEW_TASK", "MESSAGE"):
            item = {"type": "agent_message", "content": [
                    {"type": "input_text", "text": f"Message Type: {kind}\nTask name: /root/test\nSender: /root\nPayload:\n"},
                    {"type": "encrypted_content", "encrypted_content": "local-test-cipher"}]}
            self.assertEqual(recovery_helper.communication_kind(item), kind)

    def test_unknown_encrypted_item_is_rejected(self):
        item = {"type": "agent_message", "content": [
                {"type": "input_text", "text": "Message Type: UNKNOWN\n"},
                {"type": "encrypted_content", "encrypted_content": "opaque"}]}
        with self.assertRaises(ValueError):
            recovery_helper.communication_kind(item)

    def test_decompressed_request_size_is_bounded(self):
        old_limit = relay.MAX_BODY
        try:
            relay.MAX_BODY = 64
            self.assertEqual(relay.bounded_decompress(gzip.compress(b"short")), b"short")
            with self.assertRaises(relay.RelayError):
                relay.bounded_decompress(gzip.compress(b"x" * 65))
        finally:
            relay.MAX_BODY = old_limit

    def test_failed_map_write_does_not_commit_memory_entry(self):
        def fail_write(*args):
            raise OSError("Simulated full disk")
        def read_once(options, tasks):
            self.assertIsNone(options.catalog, "Native recovery must not inherit the routed catalog")
            return ["Recovered task"]
        item = {"type": "agent_message", "id": "amsg-cache", "content": [
                {"type": "input_text", "text": "Message Type: NEW_TASK\nPayload:\n"},
                {"type": "encrypted_content", "encrypted_content": "local-test-cipher"}]}
        recovery = object.__new__(relay.TaskRecovery)
        recovery.args = SimpleNamespace(auto_recover=True, codex_original=None, catalog=Path("routed-models.json"),
                                        recovery_model="native-test", recovery_map=None, timeout=1)
        recovery.lock = threading.Lock()
        recovery.task_locks = {}
        recovery.messages = {}
        recovery.helper = SimpleNamespace(communication_kind=recovery_helper.communication_kind,
                                         read_once=read_once, private_json=fail_write)
        with self.assertRaises(OSError):
            recovery.read("local-test-cipher", item)
        self.assertNotIn(hashlib.sha256(b"local-test-cipher").hexdigest(), recovery.messages)

    def test_old_task_keeps_id_envelope_and_other_history(self):
        class Recovery:
            def read(self, cipher, item):
                self.asserted = cipher == "old-cipher" and item["id"] == "amsg-old"
                return "Recovered user task"
        recovery = Recovery()
        reasoning = {"type": "reasoning", "encrypted_content": "hidden-reasoning"}
        call = {"type": "function_call", "id": "fc-old", "call_id": "call-old", "arguments": "{}"}
        original = {"input": [reasoning, call, {"type": "agent_message", "id": "amsg-old",
                    "content": [{"type": "input_text", "text": "Sender: /root\nPayload:"},
                                {"type": "encrypted_content", "encrypted_content": "old-cipher"}]}]}
        restored = relay.adapt_request(copy.deepcopy(original), recovery)
        self.assertTrue(recovery.asserted)
        self.assertEqual(restored["input"][:2], original["input"][:2])
        self.assertEqual(restored["input"][2]["id"], "amsg-old")
        self.assertEqual(restored["input"][2]["content"][0]["text"], "Sender: /root\nPayload:\nRecovered user task")
        self.assertEqual(original["input"][2]["type"], "agent_message")

    def test_missing_task_fails_without_dropping_history(self):
        class Recovery:
            def read(self, *args):
                raise relay.RelayError("missing recovery")
        value = {"input": [{"type": "agent_message", "content": [
                            {"type": "encrypted_content", "encrypted_content": "unknown-cipher"}]}]}
        before = copy.deepcopy(value)
        with self.assertRaises(relay.RelayError):
            relay.adapt_request(value, Recovery())
        self.assertEqual(value, before)

    def test_only_collaboration_transport_marker_changes(self):
        def namespace(name):
            return {"type": "namespace", "name": name, "tools": [{"type": "function", "name": "send_message",
                    "parameters": {"properties": {"message": {"type": "string", "encrypted": True},
                                   "other": {"encrypted": True}}}}]}
        value = [namespace("collaboration"), namespace("mcp__example")]
        original = copy.deepcopy(value)
        relay.clear_message_markers(value)
        self.assertNotIn("encrypted", value[0]["tools"][0]["parameters"]["properties"]["message"])
        self.assertTrue(value[0]["tools"][0]["parameters"]["properties"]["other"]["encrypted"])
        self.assertEqual(value[1], original[1])

    def test_metadata_marks_plaintext_for_added_done_and_complete(self):
        item = {"type": "function_call", "id": "fc-test", "namespace": "collaboration",
                "name": "spawn_agent", "arguments": json.dumps({"message": "Read fixture"})}
        added = {"type": "response.output_item.added", "item": copy.deepcopy(item)}
        completed = {"type": "response.completed", "response": {"output": [copy.deepcopy(item)]}}
        relay.annotate_response(added, portable_plaintext=True)
        relay.annotate_response(completed, portable_plaintext=True)
        self.assertEqual(added["item"]["encrypted_function_args"], [])
        self.assertEqual(completed["response"]["output"][0]["encrypted_function_args"], [])
        self.assertEqual(added["item"]["id"], item["id"])
        self.assertEqual(added["item"]["arguments"], item["arguments"])

    def test_ciphertext_and_external_mcp_are_not_relabelled(self):
        opaque = {"type": "function_call", "name": "send_message",
                  "arguments": json.dumps({"message": "gAAAAABopaque"})}
        with self.assertRaises(relay.RelayError):
            relay.annotate_response(opaque, portable_plaintext=True)
        self.assertNotIn("encrypted_function_args", opaque)
        external = {"type": "function_call", "namespace": "mcp__example", "name": "send_message",
                    "arguments": "{}"}
        original = copy.deepcopy(external)
        relay.annotate_response(external, portable_plaintext=True)
        self.assertEqual(external, original)
        opaque["arguments"] = "{}"
        opaque["encrypted_function_args"] = ["message"]
        with self.assertRaises(relay.RelayError):
            relay.annotate_response(opaque, portable_plaintext=True)

    def test_default_mode_preserves_encrypted_schema_and_tool_arguments(self):
        namespace = {
            "type": "namespace",
            "name": "collaboration",
            "tools": [{
                "type": "function",
                "name": "spawn_agent",
                "parameters": {"properties": {"message": {"type": "string", "encrypted": True}}},
            }],
        }
        body = {"input": [], "tools": [namespace]}
        rewritten = relay.adapt_request(copy.deepcopy(body), object())
        self.assertTrue(
            rewritten["tools"][0]["tools"][0]["parameters"]["properties"]["message"]["encrypted"]
        )

        call = {
            "type": "function_call",
            "namespace": "collaboration",
            "name": "spawn_agent",
            "arguments": json.dumps({"message": "ciphertext-producing task"}),
            "encrypted_function_args": ["opaque-envelope"],
        }
        before = copy.deepcopy(call)
        relay.annotate_response(call)
        self.assertEqual(call, before)

    def test_portable_plaintext_keeps_registered_namespace_and_clears_marker(self):
        namespace = {
            "type": "namespace",
            "name": "collaboration",
            "tools": [
                {
                    "type": "function",
                    "name": "spawn_agent",
                    "parameters": {
                        "properties": {
                            "message": {"type": "string", "encrypted": True},
                        },
                    },
                },
                {"type": "function", "name": "wait_agent", "parameters": {}},
            ],
        }
        body = {"model": "mapped-test", "input": [], "tools": [namespace]}
        rewritten = relay.adapt_request(
            copy.deepcopy(body),
            object(),
            portable_plaintext=True,
        )
        portable = rewritten["tools"][0]
        self.assertEqual(portable["name"], "collaboration")
        self.assertNotIn(
            "encrypted",
            portable["tools"][0]["parameters"]["properties"]["message"],
        )

        call = {
            "type": "function_call",
            "id": "fc-portable",
            "namespace": "collaboration",
            "name": "spawn_agent",
            "arguments": json.dumps({"message": "plain task"}),
        }
        response = {"type": "response.output_item.done", "item": call}
        relay.annotate_response(response, portable_plaintext=True)
        self.assertEqual(response["item"]["namespace"], "collaboration")
        self.assertEqual(response["item"]["encrypted_function_args"], [])
        self.assertEqual(response["item"]["arguments"], call["arguments"])

        wait_call = {
            "type": "function_call",
            "id": "fc-wait",
            "namespace": "collaboration",
            "name": "wait_agent",
            "arguments": json.dumps({"targets": ["child"], "timeout_ms": 1000}),
        }
        relay.annotate_response(wait_call, portable_plaintext=True)
        self.assertEqual(wait_call["namespace"], "collaboration")
        self.assertNotIn("encrypted_function_args", wait_call)

    def test_portable_plaintext_agent_message_becomes_standard_message(self):
        body = {
            "input": [
                {
                    "type": "agent_message",
                    "id": "amsg-portable",
                    "content": [{"type": "input_text", "text": "plain task"}],
                }
            ]
        }
        rewritten = relay.adapt_request(
            copy.deepcopy(body),
            object(),
            portable_plaintext=True,
        )
        self.assertEqual(
            rewritten["input"][0],
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "plain task"}],
                "id": "amsg-portable",
            },
        )


class HTTPContractTests(unittest.TestCase):
    def setUp(self):
        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.server.received = self.rfile.read(int(self.headers["Content-Length"]))
                self.server.path = self.path
                self.server.authorization = self.headers.get("Authorization")
                body, content_type, encoding = self.server.reply
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                if encoding:
                    self.send_header("Content-Encoding", encoding)
                self.end_headers()
                self.wfile.write(body)

        class Recovery:
            def read(self, cipher, item):
                return "Recovered user task"

        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
        self.server.aliases = {"mapped-test"}
        self.server.upstream = f"http://127.0.0.1:{self.upstream.server_port}"
        self.server.timeout = 5
        self.server.opener = urllib.request.build_opener(relay.NoRedirect())
        self.server.recovery = Recovery()
        for server in (self.upstream, self.server):
            server.daemon_threads = True
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def tearDown(self):
        for server in (self.server, self.upstream):
            server.shutdown()
            server.server_close()

    def request(self, body):
        request = urllib.request.Request(f"http://127.0.0.1:{self.server.server_port}/responses?test=1",
                                         data=body, headers={"Content-Type": "application/json",
                                                            "Authorization": "Bearer local-test-only"})
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.read(), response.headers

    def test_native_body_and_compressed_stream_are_preserved(self):
        body = b'{ "model" : "native-test", "input": [{"type":"reasoning","encrypted_content":"opaque"}] }\n'
        stream = gzip.compress(b'data: {"type":"response.output_text.delta","delta":"native"}\n\n')
        self.upstream.reply = stream, "text/event-stream", "gzip"
        received, headers = self.request(body)
        self.assertEqual(self.upstream.received, body)
        self.assertEqual(self.upstream.path, "/responses?test=1")
        self.assertEqual(self.upstream.authorization, "Bearer local-test-only")
        self.assertEqual(received, stream)
        self.assertEqual(headers["Content-Encoding"], "gzip")

    def test_mapped_http_task_and_stream_metadata_are_adapted(self):
        item = {"type": "function_call", "id": "fc-http", "namespace": "collaboration",
                "name": "spawn_agent", "arguments": json.dumps({"message": "Read fixture"})}
        event = {"type": "response.output_item.done", "item": item}
        stream = b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"
        self.upstream.reply = stream, "text/event-stream", None
        body = {"model": "mapped-test", "input": [{"type": "agent_message", "id": "amsg-http",
                 "content": [{"type": "encrypted_content", "encrypted_content": "old-cipher"}]}]}
        received, _ = self.request(json.dumps(body).encode())
        task = json.loads(self.upstream.received)["input"][0]
        self.assertEqual(task["id"], "amsg-http")
        self.assertEqual(task["role"], "user")
        self.assertEqual(task["content"][0]["text"], "Recovered user task")
        response_item = json.loads(received.splitlines()[0][6:])["item"]
        self.assertNotIn("encrypted_function_args", response_item)
        self.assertEqual(response_item["arguments"], item["arguments"])
        self.assertIn(b"data: [DONE]", received)

    def test_portable_plaintext_http_roundtrip(self):
        self.server.portable_plaintext = True
        upstream_call = {
            "type": "function_call",
            "id": "fc-portable-http",
            "namespace": "collaboration",
            "name": "spawn_agent",
            "arguments": json.dumps({"task_name": "worker", "message": "plain task"}),
        }
        stream = (
            b"data: "
            + json.dumps(
                {"type": "response.output_item.done", "item": upstream_call}
            ).encode()
            + b"\n\ndata: [DONE]\n\n"
        )
        self.upstream.reply = stream, "text/event-stream", None
        body = {
            "model": "mapped-test",
            "input": [
                {
                    "type": "agent_message",
                    "id": "amsg-portable-http",
                    "content": [{"type": "input_text", "text": "plain task"}],
                }
            ],
            "tools": [
                {
                    "type": "namespace",
                    "name": "collaboration",
                    "tools": [
                        {
                            "type": "function",
                            "name": "spawn_agent",
                            "parameters": {
                                "properties": {
                                    "message": {"type": "string", "encrypted": True},
                                },
                            },
                        }
                    ],
                }
            ],
        }
        received, _ = self.request(json.dumps(body).encode())
        forwarded = json.loads(self.upstream.received)
        self.assertEqual(forwarded["tools"][0]["name"], "collaboration")
        self.assertNotIn(
            "encrypted",
            forwarded["tools"][0]["tools"][0]["parameters"]["properties"]["message"],
        )
        self.assertEqual(forwarded["input"][0]["type"], "message")
        self.assertEqual(forwarded["input"][0]["content"][0]["text"], "plain task")
        response_item = json.loads(received.splitlines()[0][6:])["item"]
        self.assertEqual(response_item["namespace"], "collaboration")
        self.assertEqual(response_item["encrypted_function_args"], [])
        self.assertIn(b"data: [DONE]", received)

    def test_mapped_gzip_plaintext_response_is_consistently_decoded(self):
        self.upstream.reply = gzip.compress(b"Plain HTTP diagnostic"), "text/plain", "gzip"
        received, headers = self.request(b'{"model":"mapped-test","input":[]}')
        self.assertEqual(received, b"Plain HTTP diagnostic")
        self.assertNotIn("Content-Encoding", headers)

    def test_mapped_gzip_sse_response_is_decoded_and_adapted(self):
        item = {"type": "function_call", "id": "fc-gzip", "name": "send_message",
                "namespace": "collaboration", "arguments": '{"message":"Readable task"}',
                "encrypted_function_args": ["opaque-envelope"]}
        event = {"type": "response.output_item.done", "item": item}
        stream = b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"
        self.upstream.reply = gzip.compress(stream), "text/event-stream", "gzip"
        received, headers = self.request(b'{"model":"mapped-test","input":[]}')
        self.assertEqual(json.loads(received.splitlines()[0][6:])["item"]["encrypted_function_args"],
                         ["opaque-envelope"])
        self.assertNotIn("Content-Encoding", headers)

    def test_recovery_has_no_tools_and_blocks_upstream_tool_call(self):
        self.server.recovery_guard = True
        item = {"type": "function_call", "id": "fc-block", "name": "forbidden_exec", "arguments": "{}"}
        event = {"type": "response.output_item.added", "item": item}
        self.upstream.reply = b"data: " + json.dumps(event).encode() + b"\n\n", "text/event-stream", None
        body = {"model": "native-test", "input": [{"type": "additional_tools", "id": "at-guard",
                "role": "developer", "tools": [{"name": "forbidden_exec"}]}],
                "tools": [{"type": "function", "name": "forbidden_exec"}],
                "tool_choice": "auto"}
        received, _ = self.request(json.dumps(body).encode())
        rewritten = json.loads(self.upstream.received)
        self.assertEqual(rewritten["tools"], [])
        self.assertEqual(rewritten["tool_choice"], "none")
        self.assertEqual(rewritten["input"][0]["tools"], [])
        self.assertEqual(rewritten["input"][0]["id"], "at-guard")
        self.assertNotIn(b"forbidden_exec", received)
        self.assertIn(b"blocked before local dispatch", received)


if __name__ == "__main__":
    unittest.main()
