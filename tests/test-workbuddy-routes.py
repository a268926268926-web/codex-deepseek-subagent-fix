#!/usr/bin/env python3
"""Independent black-box contracts for the WorkBuddy model routes and upstream policy."""
import gzip
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock
import urllib.request

sys.dont_write_bytecode = True

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
WORKBUDDY_ALIAS = "gpt-5.6-luna"
WORKBUDDY_TARGET = "ccs-testkey/cn:deepseek-v4.1-flash"
NATIVE_6LUNA = "gpt-6-luna"
NATIVE_61SOL = "gpt-6.1-sol"


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


relay = load_module("workbuddy_relay_under_test", "routed-v2-relay.py")
launcher = load_module("workbuddy_launcher_under_test", "launch-routed-codex.py")


def message_item(identifier="fc-1"):
    return {"type": "function_call", "id": identifier, "call_id": "call-" + identifier,
            "namespace": "collaboration", "name": "send_message",
            "arguments": json.dumps({"message": "contract task"})}


class RouteSpecificationTests(unittest.TestCase):
    def test_model_routes_accept_alias_target_pairs_and_empty_default(self):
        self.assertEqual(relay.parse_model_routes([]), {})
        routes = relay.parse_model_routes([WORKBUDDY_ALIAS + "=" + WORKBUDDY_TARGET,
                                           "gpt-5.6-sol=ccs-testkey/cn:deepseek-v4.1-pro",
                                           "cn:deepseek-v4.1-flash=" + WORKBUDDY_TARGET])
        self.assertEqual(routes, {WORKBUDDY_ALIAS: WORKBUDDY_TARGET,
                                  "gpt-5.6-sol": "ccs-testkey/cn:deepseek-v4.1-pro",
                                  "cn:deepseek-v4.1-flash": WORKBUDDY_TARGET})

    def test_model_routes_reject_malformed_or_duplicate_specs(self):
        for specification in ("", "=", "no-separator", "a=b=c", "bad alias=target",
                              "alias=bad target", "-alias=target", "alias=", "=target",
                              "alias=target with space"):
            with self.subTest(specification=specification):
                with self.assertRaises(relay.RelayError):
                    relay.parse_model_routes([specification])
        with self.assertRaisesRegex(relay.RelayError, "Duplicate"):
            relay.parse_model_routes(["alias=one", "alias=two"])

    def test_upstream_accepts_https_and_loopback_http_only(self):
        accepted = {
            "https://api.example.com/v1/": "https://api.example.com/v1",
            "http://127.0.0.1:43541": "http://127.0.0.1:43541",
            "http://localhost:9": "http://localhost:9",
            "http://LOCALHOST:9": "http://LOCALHOST:9",
            "http://[::1]:8080": "http://[::1]:8080",
        }
        for url, expected in accepted.items():
            with self.subTest(url=url):
                self.assertEqual(relay.validated_upstream(url), expected)

    def test_upstream_rejects_remote_http_and_disguised_loopback(self):
        rejected = ("http://example.com/v1", "http://127.0.0.1.nip.io:8080",
                    "http://localhost.evil.example:80", "http://127.0.0.1:8080@evil.example/",
                    "http://user:pass@127.0.0.1:8080", "http://127.0.0.1:8080/?token=1",
                    "http://127.0.0.1:8080/#fragment", "https://user@example.com/v1",
                    "https://example.com/v1?q=1", "https://example.com/v1#f", "ftp://127.0.0.1",
                    "http://127.0.0.1 :8080", "https://example.com\\v1", "https://", "")
        for url in rejected:
            with self.subTest(url=url):
                with self.assertRaises(relay.RelayError):
                    relay.validated_upstream(url)
        with self.assertRaises(relay.RelayError):
            relay.validated_upstream(1234)


class RelayCommandLineTests(unittest.TestCase):
    def run_relay(self, directory, *arguments):
        command = [sys.executable, str(SCRIPTS / "routed-v2-relay.py"),
                   "--recovery-map", str(directory / "recovery-map.json"),
                   "--codex-original", str(directory / "codex"),
                   "--ready-file", str(directory / "ready.json"),
                   *arguments]
        return subprocess.run(command, capture_output=True, text=True, timeout=30)

    def test_invalid_routes_and_remote_http_are_rejected_before_startup(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result = self.run_relay(directory, "--upstream", "https://api.example.com/v1",
                                    "--alias", WORKBUDDY_ALIAS, "--model-route", "missing-separator")
            self.assertEqual(result.returncode, 2)
            self.assertIn("alias=target", result.stderr)

            result = self.run_relay(directory, "--upstream", "http://example.com/v1",
                                    "--alias", WORKBUDDY_ALIAS)
            self.assertEqual(result.returncode, 2)
            self.assertIn("loopback", result.stderr)

            result = self.run_relay(directory, "--upstream", "https://api.example.com/v1")
            self.assertEqual(result.returncode, 2)
            self.assertIn("routed alias", result.stderr)

    def test_aggregation_without_native_profile_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result = self.run_relay(directory, "--upstream", "http://127.0.0.1:9",
                                    "--alias", WORKBUDDY_ALIAS, "--auto-recover")
            self.assertEqual(result.returncode, 2)
            self.assertIn("native-profile", result.stderr)

            result = self.run_relay(directory, "--upstream", "https://api.example.com/v1",
                                    "--model-route", WORKBUDDY_ALIAS + "=" + WORKBUDDY_TARGET,
                                    "--auto-recover")
            self.assertEqual(result.returncode, 2)
            self.assertIn("native-profile", result.stderr)


class LocalUpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        pass

    def reply(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.server.received = self.rfile.read(length) if length else b""
        self.server.received_headers = {key.lower(): value for key, value in self.headers.items()}
        self.server.received_path = self.path
        body, content_type, encoding = self.server.response
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.reply()

    def do_GET(self):
        self.reply()


class RelayHTTPTestCase(unittest.TestCase):
    class Recovery:
        def __init__(self):
            self.calls = []

        def read(self, cipher, item):
            self.calls.append((cipher, item.get("id")))
            return "Recovered task text"

    def setUp(self):
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), LocalUpstreamHandler)
        self.upstream.daemon_threads = True
        self.upstream.response = (b"ok", "text/plain", None)
        threading.Thread(target=self.upstream.serve_forever, daemon=True).start()
        self.relays = []

    def tearDown(self):
        for server in self.relays:
            server.shutdown()
            server.server_close()
        self.upstream.shutdown()
        self.upstream.server_close()

    def relay_server(self, aliases, routes=None, guard=False, portable=False):
        server = ThreadingHTTPServer(("127.0.0.1", 0), relay.Handler)
        server.daemon_threads = True
        server.aliases = set(aliases)
        server.portable_plaintext = portable
        if routes is not None:
            server.model_routes = dict(routes)
        server.upstream = "http://127.0.0.1:%d" % self.upstream.server_port
        server.timeout = 5
        server.opener = urllib.request.build_opener(relay.NoRedirect())
        server.recovery = self.Recovery()
        if guard:
            server.recovery_guard = True
        self.relays.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def request(self, server, body, content_type="application/json", encoding=None,
                accept_encoding=None):
        headers = {"Content-Type": content_type}
        if encoding:
            headers["Content-Encoding"] = encoding
        if accept_encoding:
            headers["Accept-Encoding"] = accept_encoding
        request = urllib.request.Request("http://127.0.0.1:%d/responses?route=1" % server.server_port,
                                         data=body, headers=headers)
        with urllib.request.urlopen(request, timeout=10) as response:
            return (response.read(),
                    {key.lower(): value for key, value in response.headers.items()})


class NativePassthroughTests(RelayHTTPTestCase):
    def test_native_request_with_routes_preserves_body_model_and_unknown_fields(self):
        server = self.relay_server(set(), routes={WORKBUDDY_ALIAS: WORKBUDDY_TARGET})
        body = json.dumps({"model": NATIVE_6LUNA, "stream": True,
                           "unknown_field": {"keep": [1, 2, 3]},
                           "input": [{"type": "reasoning", "encrypted_content": "opaque"}]},
                          separators=(",", ":")).encode() + b"\n"
        compressed = gzip.compress(body)
        stream = gzip.compress(b'data: {"type":"response.output_text.delta","delta":"native"}\n\n')
        self.upstream.response = (stream, "text/event-stream", "gzip")
        received, headers = self.request(server, compressed, encoding="gzip",
                                         accept_encoding="gzip")
        self.assertEqual(self.upstream.received, compressed)
        self.assertEqual(self.upstream.received_headers.get("content-encoding"), "gzip")
        self.assertEqual(self.upstream.received_headers.get("accept-encoding"), "gzip")
        self.assertEqual(self.upstream.received_path, "/responses?route=1")
        self.assertEqual(gzip.decompress(self.upstream.received), body)
        self.assertEqual(received, stream)
        self.assertEqual(headers.get("content-encoding"), "gzip")

    def test_native_sol_and_route_target_models_are_never_rewritten(self):
        server = self.relay_server(set(), routes={WORKBUDDY_ALIAS: WORKBUDDY_TARGET})
        self.upstream.response = (b'{"output":[]}', "application/json", None)
        for model in (NATIVE_61SOL, "gpt-5.6-sol", NATIVE_6LUNA, WORKBUDDY_TARGET):
            body = json.dumps({"model": model, "input": [
                {"type": "message", "role": "user",
                 "content": [{"type": "input_text", "text": "native"}]}]},
                separators=(",", ":")).encode()
            with self.subTest(model=model):
                self.request(server, body)
                self.assertEqual(self.upstream.received, body)


class WorkBuddyRouteTests(RelayHTTPTestCase):
    def workbuddy_body(self):
        return {
            "model": WORKBUDDY_ALIAS,
            "stream": True,
            "store": False,
            "include": ["reasoning.encrypted_content"],
            "client_metadata": {"origin": "contract-test", "nested": [1, 2, 3]},
            "input": [
                {"type": "message", "role": "user",
                 "content": [{"type": "input_text", "text": "hello"}]},
                {"type": "agent_message", "id": "amsg-plain",
                 "content": [{"type": "input_text", "text": "Message Type: MESSAGE"}]},
                {"type": "agent_message", "id": "amsg-encrypted",
                 "content": [{"type": "input_text", "text": "Sender: /root\nPayload:"},
                             {"type": "encrypted_content", "encrypted_content": "local-test-cipher"}]},
                {"type": "additional_tools", "id": "at-1", "role": "developer",
                 "tools": [{"name": "exec_command"}]},
            ],
            "tools": [{"type": "namespace", "name": "collaboration", "tools": [
                {"type": "function", "name": "send_message",
                 "parameters": {"properties": {
                     "message": {"type": "string", "encrypted": True},
                     "other": {"type": "string", "encrypted": True}}}}]}],
            "tool_choice": "auto",
        }

    def test_workbuddy_alias_rewrites_only_model_and_keeps_responses_shape(self):
        server = self.relay_server({WORKBUDDY_ALIAS},
                                   routes={WORKBUDDY_ALIAS: WORKBUDDY_TARGET},
                                   portable=True)
        body = self.workbuddy_body()
        received, _ = self.request(server, json.dumps(body).encode())
        sent = json.loads(self.upstream.received)
        self.assertEqual(received, b"ok")
        self.assertEqual(sent["model"], WORKBUDDY_TARGET)
        self.assertIs(sent["stream"], True)
        self.assertIs(sent["store"], False)
        self.assertEqual(sent["include"], ["reasoning.encrypted_content"])
        self.assertEqual(sent["client_metadata"], body["client_metadata"])
        self.assertEqual(sent["tool_choice"], "auto")
        self.assertEqual(sent["input"][0], body["input"][0])
        self.assertEqual(sent["input"][1], {"type": "message", "role": "user", "id": "amsg-plain",
                                            "content": [{"type": "input_text",
                                                         "text": "Message Type: MESSAGE"}]})
        self.assertEqual(sent["input"][2]["type"], "message")
        self.assertEqual(sent["input"][2]["id"], "amsg-encrypted")
        self.assertEqual(sent["input"][2]["content"],
                         [{"type": "input_text",
                           "text": "Sender: /root\nPayload:\nRecovered task text"}])
        self.assertEqual(sent["input"][3], body["input"][3])
        properties = sent["tools"][0]["tools"][0]["parameters"]["properties"]
        self.assertEqual(properties["message"], {"type": "string"})
        self.assertEqual(properties["other"], {"type": "string", "encrypted": True})
        self.assertEqual(server.recovery.calls, [("local-test-cipher", "amsg-encrypted")])

    def test_route_alias_alone_selects_mapping_without_alias_flag(self):
        server = self.relay_server(set(), routes={WORKBUDDY_ALIAS: WORKBUDDY_TARGET})
        self.upstream.response = (b'{"output":[]}', "application/json", None)
        body = {"model": WORKBUDDY_ALIAS, "input": [
            {"type": "agent_message", "id": "amsg-route-only",
             "content": [{"type": "encrypted_content", "encrypted_content": "route-only-cipher"}]}]}
        self.request(server, json.dumps(body).encode())
        sent = json.loads(self.upstream.received)
        self.assertEqual(sent["model"], WORKBUDDY_TARGET)
        self.assertEqual(sent["input"][0]["type"], "message")
        self.assertEqual(sent["input"][0]["content"][0]["text"], "Recovered task text")

    def test_legacy_alias_adapts_without_rewriting_its_model(self):
        server = self.relay_server({"gpt-5.6-sol"},
                                   routes={WORKBUDDY_ALIAS: WORKBUDDY_TARGET})
        self.upstream.response = (b'{"output":[]}', "application/json", None)
        body = {"model": "gpt-5.6-sol", "input": [
            {"type": "agent_message", "id": "amsg-legacy",
             "content": [{"type": "encrypted_content", "encrypted_content": "legacy-cipher"}]}]}
        self.request(server, json.dumps(body).encode())
        sent = json.loads(self.upstream.received)
        self.assertEqual(sent["model"], "gpt-5.6-sol")
        self.assertEqual(sent["input"][0]["type"], "message")
        self.assertEqual(sent["input"][0]["content"][0]["text"], "Recovered task text")

    def test_message_tool_annotations_apply_to_json_and_sse(self):
        server = self.relay_server({WORKBUDDY_ALIAS},
                                   routes={WORKBUDDY_ALIAS: WORKBUDDY_TARGET},
                                   portable=True)
        item = message_item("fc-json")
        other = {"type": "function_call", "id": "fc-other", "namespace": "functions",
                 "name": "exec_command", "arguments": "{}"}
        self.upstream.response = (json.dumps({"id": "resp-1", "output": [item, other]}).encode(),
                                  "application/json", None)
        body = {"model": WORKBUDDY_ALIAS, "input": []}
        received, _ = self.request(server, json.dumps(body).encode())
        value = json.loads(received)
        self.assertEqual(value["output"][0]["encrypted_function_args"], [])
        self.assertEqual(value["output"][0]["arguments"], item["arguments"])
        self.assertEqual(value["output"][0]["id"], "fc-json")
        self.assertNotIn("encrypted_function_args", value["output"][1])

        events = [{"type": "response.output_item.added", "item": message_item("fc-sse")},
                  {"type": "response.function_call_arguments.delta", "item_id": "fc-sse",
                   "delta": '{"message":'}]
        stream = b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)
        stream += b"data: [DONE]\n\n"
        self.upstream.response = (stream, "text/event-stream", None)
        received, _ = self.request(server, json.dumps(body).encode())
        lines = [line for line in received.split(b"\n") if line.startswith(b"data: ")]
        first = json.loads(lines[0][6:])
        self.assertEqual(first["item"]["encrypted_function_args"], [])
        self.assertEqual(first["item"]["id"], "fc-sse")
        self.assertEqual(json.loads(lines[1][6:])["item_id"], "fc-sse")
        self.assertIn(b"data: [DONE]", received)

    def test_mapped_gzip_request_and_response_are_normalized(self):
        server = self.relay_server({WORKBUDDY_ALIAS},
                                   routes={WORKBUDDY_ALIAS: WORKBUDDY_TARGET},
                                   portable=True)
        payload = gzip.compress(json.dumps({"output": [message_item("fc-gzip")]}).encode())
        self.upstream.response = (payload, "application/json", "gzip")
        body = gzip.compress(json.dumps({"model": WORKBUDDY_ALIAS, "input": []}).encode())
        received, headers = self.request(server, body, encoding="gzip")
        sent = json.loads(self.upstream.received)
        self.assertEqual(sent["model"], WORKBUDDY_TARGET)
        self.assertNotIn("content-encoding", self.upstream.received_headers)
        self.assertEqual(json.loads(received)["output"][0]["encrypted_function_args"], [])
        self.assertNotIn("content-encoding", headers)


class GuardCompatibilityTests(RelayHTTPTestCase):
    def guard_body(self, model):
        return {"model": model, "tools": [{"type": "function", "name": "exec_command"}],
                "tool_choice": "auto",
                "input": [{"type": "additional_tools", "id": "at-1", "role": "developer",
                           "tools": [{"name": "exec_command"}]}]}

    def assert_guard_response(self, server, model):
        self.upstream.response = (b'{"output":[]}', "application/json", None)
        received, _ = self.request(server, json.dumps(self.guard_body(model)).encode())
        sent = json.loads(self.upstream.received)
        self.assertEqual(sent["model"], model)
        self.assertEqual(sent["tools"], [])
        self.assertEqual(sent["tool_choice"], "none")
        self.assertEqual(sent["input"][0]["tools"], [])
        self.assertEqual(sent["input"][0]["id"], "at-1")
        self.assertEqual(json.loads(received), {"output": []})

    def test_guard_never_rewrites_model_with_or_without_routes(self):
        plain = self.relay_server(set(), guard=True)
        self.assert_guard_response(plain, WORKBUDDY_ALIAS)
        routed = self.relay_server({WORKBUDDY_ALIAS},
                                   routes={WORKBUDDY_ALIAS: WORKBUDDY_TARGET}, guard=True)
        self.assert_guard_response(routed, WORKBUDDY_ALIAS)

    def test_legacy_configuration_without_model_routes_still_maps(self):
        server = self.relay_server({"mapped-test"})
        self.upstream.response = (b'{"output":[]}', "application/json", None)
        body = {"model": "mapped-test", "input": [
            {"type": "agent_message", "id": "amsg-legacy",
             "content": [{"type": "encrypted_content", "encrypted_content": "legacy-cipher"}]}]}
        self.request(server, json.dumps(body).encode())
        sent = json.loads(self.upstream.received)
        self.assertEqual(sent["model"], "mapped-test")
        self.assertEqual(sent["input"][0]["type"], "message")
        self.assertEqual(sent["input"][0]["content"][0]["text"], "Recovered task text")


class RelayProcessIntegrationTests(RelayHTTPTestCase):
    def start_relay(self, directory, *extra):
        ready = directory / "ready.json"
        command = [sys.executable, str(SCRIPTS / "routed-v2-relay.py"),
                   "--recovery-map", str(directory / "recovery-map.json"),
                   "--codex-original", str(directory / "codex"),
                   "--ready-file", str(ready), *extra]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 20
            while not ready.exists():
                self.assertIsNone(process.poll(), "Relay exited before becoming ready")
                self.assertLess(time.monotonic(), deadline, "Relay did not become ready")
                time.sleep(0.05)
        except BaseException:
            process.terminate()
            process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()
            raise
        return process, json.loads(ready.read_text())["url"]

    def stop_relay(self, process):
        process.terminate()
        process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()

    def post(self, url, body):
        request = urllib.request.Request(url + "/responses", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 200)

    def test_relay_process_routes_alias_without_alias_flag(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            process, url = self.start_relay(
                directory, "--upstream", "http://127.0.0.1:%d" % self.upstream.server_port,
                "--model-route", WORKBUDDY_ALIAS + "=" + WORKBUDDY_TARGET)
            try:
                self.upstream.response = (b'{"output":[]}', "application/json", None)
                body = {"model": WORKBUDDY_ALIAS, "input": [
                    {"type": "agent_message", "id": "amsg-process",
                     "content": [{"type": "input_text", "text": "Message Type: NEW_TASK"}]}]}
                self.post(url, body)
                sent = json.loads(self.upstream.received)
                self.assertEqual(sent["model"], WORKBUDDY_TARGET)
                self.assertEqual(sent["input"][0]["type"], "message")
                self.assertEqual(sent["input"][0]["content"][0]["text"], "Message Type: NEW_TASK")
            finally:
                self.stop_relay(process)

    def test_relay_process_https_alias_only_autorecover_needs_no_pin(self):
        with tempfile.TemporaryDirectory() as temporary:
            process, _ = self.start_relay(
                Path(temporary), "--upstream", "https://api.example.com/v1",
                "--alias", "gpt-5.6-sol", "--auto-recover")
            self.stop_relay(process)


class LauncherRoutesConfigTests(unittest.TestCase):
    def write_routes(self, directory, document):
        path = directory / "routed-providers.local.json"
        path.write_text(json.dumps(document))
        return path

    def make_executables(self, directory):
        codex_original = directory / "codex"
        app = directory / "app"
        for path in (codex_original, app):
            path.write_text("test executable")
            path.chmod(0o700)
        return codex_original, app

    def test_load_routes_config_none_and_valid_document(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.assertEqual(launcher.load_routes_config(None), ({}, None))
            path = self.write_routes(directory, {
                "version": 1,
                "model_routes": {WORKBUDDY_ALIAS: WORKBUDDY_TARGET,
                                 "gpt-5.6-sol": "ccs-testkey/cn:deepseek-v4.1-pro"},
                "native_profile": "profiles/native-provider.json"})
            routes, profile = launcher.load_routes_config(path)
            self.assertEqual(routes, {WORKBUDDY_ALIAS: WORKBUDDY_TARGET,
                                      "gpt-5.6-sol": "ccs-testkey/cn:deepseek-v4.1-pro"})
            self.assertEqual(profile, (directory / "profiles" / "native-provider.json").resolve())
            routes, profile = launcher.load_routes_config(
                self.write_routes(directory, {"version": 1}))
            self.assertEqual((routes, profile), ({}, None))

    def test_load_routes_config_rejects_missing_invalid_and_unknown_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with self.assertRaisesRegex(ValueError, "missing or invalid"):
                launcher.load_routes_config(directory / "absent.json")
            broken = directory / "broken.json"
            broken.write_text("{not json")
            with self.assertRaisesRegex(ValueError, "missing or invalid"):
                launcher.load_routes_config(broken)
            bad_documents = [
                [],
                {"version": 2, "model_routes": {}},
                {"version": True, "model_routes": {}},
                {"version": 1, "model_routes": {}, "extra": 1},
                {"version": 1, "model_routes": []},
                {"version": 1, "model_routes": {WORKBUDDY_ALIAS: 5}},
                {"version": 1, "model_routes": {"bad alias": WORKBUDDY_TARGET}},
                {"version": 1, "model_routes": {WORKBUDDY_ALIAS: ""}},
                {"version": 1, "native_profile": "   "},
            ]
            for document in bad_documents:
                with self.subTest(document=document):
                    with self.assertRaises(ValueError):
                        launcher.load_routes_config(self.write_routes(directory, document))

    def test_load_routes_config_upstream_override_is_opt_in(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.assertEqual(launcher.load_routes_config(None, include_upstream=True), ({}, None, None))
            document = {"version": 1, "model_routes": {WORKBUDDY_ALIAS: WORKBUDDY_TARGET},
                        "native_profile": "native-profile.json",
                        "upstream": "http://127.0.0.1:43541/"}
            path = self.write_routes(directory, document)
            routes, profile = launcher.load_routes_config(path)
            self.assertEqual(routes, {WORKBUDDY_ALIAS: WORKBUDDY_TARGET})
            self.assertEqual(profile, (directory / "native-profile.json").resolve())
            routes, profile, upstream = launcher.load_routes_config(path, include_upstream=True)
            self.assertEqual(routes, {WORKBUDDY_ALIAS: WORKBUDDY_TARGET})
            self.assertEqual(profile, (directory / "native-profile.json").resolve())
            self.assertEqual(upstream, "http://127.0.0.1:43541")
            routes, profile, upstream = launcher.load_routes_config(
                self.write_routes(directory, {"version": 1, "upstream": "https://api.example.com/v1"}),
                include_upstream=True)
            self.assertEqual((routes, profile, upstream),
                             ({}, None, "https://api.example.com/v1"))

    def test_load_routes_config_rejects_remote_http_upstream_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for upstream in ("http://example.com/v1", "http://127.0.0.1.nip.io:80",
                             "http://user:pass@127.0.0.1:80", "http://127.0.0.1:80/?q=1",
                             "https://api.example.com/v1#fragment", 5, "", "   ", None):
                with self.subTest(upstream=upstream):
                    document = {"version": 1, "upstream": upstream}
                    with self.assertRaises(ValueError):
                        launcher.load_routes_config(self.write_routes(directory, document),
                                                    include_upstream=True)

    def make_validate_fixture(self, directory, base_url, models, routes_document):
        codex_original, app = self.make_executables(directory)
        config_home = directory / "config-home"
        config_home.mkdir(exist_ok=True)
        (config_home / "config.toml").write_text(
            'model_provider = "custom"\n[model_providers.custom]\n'
            'base_url = "%s"\nwire_api = "responses"\n' % base_url)
        catalog = directory / "models.json"
        catalog.write_text(json.dumps({"models": models}))
        routes_config = directory / "routed-providers.local.json"
        routes_config.write_text(json.dumps(routes_document))
        args = SimpleNamespace(codex_original=codex_original, app=app, catalog=catalog,
                               recovery_map=directory / "missing-recovery.json",
                               alias=["gpt-5.6-sol"], routes_config=routes_config)
        return args, config_home

    def sibling(self, real_sibling, name, filename):
        if name == "routed_native_profile":
            return SimpleNamespace(load_native_profile=lambda path: None)
        return real_sibling(name, filename)

    def test_validate_loads_routes_config_and_expands_aliases(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            models = [{"slug": "gpt-5.6-sol", "tool_mode": "code_mode",
                       "multi_agent_version": "v2"},
                      {"slug": WORKBUDDY_ALIAS, "tool_mode": "code_mode",
                       "multi_agent_version": "v2"},
                      {"slug": NATIVE_61SOL, "tool_mode": "code_mode_only",
                       "multi_agent_version": "v1"},
                      {"slug": NATIVE_6LUNA, "tool_mode": "code_mode_only",
                       "multi_agent_version": "v1"}]
            args, config_home = self.make_validate_fixture(
                directory, "http://127.0.0.1:43541", models,
                {"version": 1, "model_routes": {WORKBUDDY_ALIAS: WORKBUDDY_TARGET},
                 "native_profile": "native-profile.json"})
            version = SimpleNamespace(stdout="codex-cli %s\n" % launcher.VERSION)
            real_sibling = launcher.sibling_module
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(config_home)}), \
                    mock.patch.object(launcher.subprocess, "run", return_value=version), \
                    mock.patch.object(launcher, "sibling_module",
                                      side_effect=lambda name, filename:
                                      self.sibling(real_sibling, name, filename)):
                provider, upstream, cached = launcher.validate(args)
            self.assertEqual((provider, upstream, cached),
                             ("custom", "http://127.0.0.1:43541", 0))
            self.assertEqual(args.model_routes, {WORKBUDDY_ALIAS: WORKBUDDY_TARGET})
            self.assertEqual(args.alias, ["gpt-5.6-sol", WORKBUDDY_ALIAS])
            self.assertEqual(args.native_profile, (directory / "native-profile.json").resolve())

    def test_validate_prefers_routes_config_upstream_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            models = [{"slug": "gpt-5.6-sol", "tool_mode": "code_mode",
                       "multi_agent_version": "v2"}]
            version = SimpleNamespace(stdout="codex-cli %s\n" % launcher.VERSION)
            real_sibling = launcher.sibling_module
            # Global provider stays HTTPS; the private config redirects the relay to loopback.
            args, config_home = self.make_validate_fixture(
                directory, "https://api.example.com/v1", models,
                {"version": 1, "upstream": "http://127.0.0.1:43541"})
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(config_home)}), \
                    mock.patch.object(launcher.subprocess, "run", return_value=version):
                with self.assertRaisesRegex(ValueError, "native_profile"):
                    launcher.validate(args)

            # Global provider is loopback but the override is HTTPS with no routes: no pin needed.
            args, config_home = self.make_validate_fixture(
                directory, "http://127.0.0.1:9", models,
                {"version": 1, "upstream": "https://api.example.com/v1"})
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(config_home)}), \
                    mock.patch.object(launcher.subprocess, "run", return_value=version), \
                    mock.patch.object(launcher, "sibling_module",
                                      side_effect=lambda name, filename:
                                      self.sibling(real_sibling, name, filename)):
                provider, upstream, cached = launcher.validate(args)
            self.assertEqual(upstream, "https://api.example.com/v1")
            self.assertIsNone(args.native_profile)

            # Routes present: the override still wins and the pin rule stays in force.
            routes, profile, upstream = launcher.load_routes_config(
                self.write_routes(directory, {
                    "version": 1,
                    "model_routes": {WORKBUDDY_ALIAS: WORKBUDDY_TARGET},
                    "native_profile": "native-profile.json",
                    "upstream": "http://127.0.0.1:43541"}),
                include_upstream=True)
            self.assertEqual(upstream, "http://127.0.0.1:43541")
            self.assertEqual(routes, {WORKBUDDY_ALIAS: WORKBUDDY_TARGET})
            self.assertEqual(profile, (directory / "native-profile.json").resolve())

    def test_validate_requires_native_profile_for_routes_and_loopback(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            models = [{"slug": "gpt-5.6-sol", "tool_mode": "code_mode",
                       "multi_agent_version": "v2"},
                      {"slug": WORKBUDDY_ALIAS, "tool_mode": "code_mode",
                       "multi_agent_version": "v2"}]
            args, config_home = self.make_validate_fixture(
                directory, "http://127.0.0.1:43541", models,
                {"version": 1, "model_routes": {WORKBUDDY_ALIAS: WORKBUDDY_TARGET}})
            version = SimpleNamespace(stdout="codex-cli %s\n" % launcher.VERSION)
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(config_home)}), \
                    mock.patch.object(launcher.subprocess, "run", return_value=version):
                with self.assertRaisesRegex(ValueError, "native_profile"):
                    launcher.validate(args)

    def test_validate_requires_route_alias_as_code_mode_v2_catalog_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            version = SimpleNamespace(stdout="codex-cli %s\n" % launcher.VERSION)
            real_sibling = launcher.sibling_module
            for model_entry in ({"slug": "gpt-5.6-sol", "tool_mode": "code_mode",
                                 "multi_agent_version": "v2"},
                                {"slug": WORKBUDDY_ALIAS, "tool_mode": "direct",
                                 "multi_agent_version": "v2"},
                                {"slug": WORKBUDDY_ALIAS, "tool_mode": "code_mode",
                                 "multi_agent_version": "v1"}):
                models = [{"slug": "gpt-5.6-sol", "tool_mode": "code_mode",
                           "multi_agent_version": "v2"}, model_entry]
                args, config_home = self.make_validate_fixture(
                    directory, "http://127.0.0.1:43541", models,
                    {"version": 1, "model_routes": {WORKBUDDY_ALIAS: WORKBUDDY_TARGET},
                     "native_profile": "native-profile.json"})
                with self.subTest(model_entry=model_entry), \
                        mock.patch.dict("os.environ", {"CODEX_HOME": str(config_home)}), \
                        mock.patch.object(launcher.subprocess, "run", return_value=version), \
                        mock.patch.object(launcher, "sibling_module",
                                          side_effect=lambda name, filename:
                                          self.sibling(real_sibling, name, filename)):
                    with self.assertRaisesRegex(ValueError, "Code Mode V2 entry"):
                        launcher.validate(args)

    def test_validate_default_without_routes_config_keeps_old_behavior(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            codex_original, app = self.make_executables(directory)
            config_home = directory / "config-home"
            config_home.mkdir()
            (config_home / "config.toml").write_text(
                'model_provider = "custom"\n[model_providers.custom]\n'
                'base_url = "https://api.example.com/v1"\nwire_api = "responses"\n')
            catalog = directory / "models.json"
            catalog.write_text(json.dumps({"models": [
                {"slug": "gpt-5.6-sol", "tool_mode": "code_mode",
                 "multi_agent_version": "v2"}]}))
            args = SimpleNamespace(codex_original=codex_original, app=app, catalog=catalog,
                                   recovery_map=directory / "missing-recovery.json",
                                   alias=["gpt-5.6-sol"], routes_config=None)
            version = SimpleNamespace(stdout="codex-cli %s\n" % launcher.VERSION)
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(config_home)}), \
                    mock.patch.object(launcher.subprocess, "run", return_value=version):
                provider, upstream, cached = launcher.validate(args)
            self.assertEqual((provider, upstream, cached),
                             ("custom", "https://api.example.com/v1", 0))
            self.assertEqual(args.model_routes, {})
            self.assertEqual(args.alias, ["gpt-5.6-sol"])
            self.assertIsNone(args.native_profile)

    def test_wrapper_keeps_desktop_overrides_last_and_all_v2_features(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            original = directory / "original cli"
            original.write_text("#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n")
            original.chmod(0o700)
            catalog = directory / "catalog with spaces.json"
            wrapper = directory / "wrapper.sh"
            wrapper.write_text(launcher.wrapper_text(original, "custom",
                                                     "http://127.0.0.1:43541", catalog))
            wrapper.chmod(0o700)
            desktop = ["-c", "features.code_mode_host=true", "app-server", "--analytics-default-enabled"]
            result = subprocess.run([str(wrapper), *desktop], capture_output=True, text=True, check=True)
            actual = json.loads(result.stdout)
            self.assertEqual(actual[:3], desktop[:3])
            self.assertEqual(actual[-1:], desktop[3:])
            overrides = actual[3:-1]
            self.assertTrue(all(overrides[i] == "-c" for i in range(0, len(overrides), 2)))
            values = overrides[1::2]
            for expected in ('model_providers.custom.base_url="http://127.0.0.1:43541"',
                             'model_catalog_json=%s' % json.dumps(str(catalog)),
                             "features.code_mode_host=true",
                             "features.multi_agent=true",
                             "features.multi_agent_v2.enabled=true",
                             "features.multi_agent_v2.hide_spawn_agent_metadata=false",
                             "features.multi_agent_v2.expose_spawn_agent_model_overrides=true",
                             "features.multi_agent_v2.wait_agent_enabled=true"):
                self.assertIn(expected, values)
            text = wrapper.read_text()
            self.assertIn('os.environ.pop("CODEX_V2_PLAINTEXT_MESSAGES", None)', text)
            self.assertIn('os.environ.pop("CODEX_V2_RECOVERY_MAP", None)', text)
            self.assertNotIn("export CODEX_V2", text)

    def test_validate_loads_real_native_profile_and_rejects_loose_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            models = [{"slug": "gpt-5.6-sol", "tool_mode": "code_mode",
                       "multi_agent_version": "v2"}]
            profile = directory / "native-profile.json"
            profile.write_text(json.dumps({"version": 1,
                                           "base_url": "https://api.example.com/v1",
                                           "api_key": "local-test-placeholder-not-a-credential",
                                           "provider_name": "Synthetic Native"}))
            profile.chmod(0o600)
            args, config_home = self.make_validate_fixture(
                directory, "https://api.example.com/v1", models, {"version": 1})
            args.native_profile = profile
            version = SimpleNamespace(stdout="codex-cli %s\n" % launcher.VERSION)
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(config_home)}), \
                    mock.patch.object(launcher.subprocess, "run", return_value=version):
                provider, upstream, cached = launcher.validate(args)
            self.assertEqual((provider, upstream, cached),
                             ("custom", "https://api.example.com/v1", 0))
            self.assertEqual(args.native_profile, profile.resolve())
            profile.chmod(0o644)
            with mock.patch.dict("os.environ", {"CODEX_HOME": str(config_home)}), \
                    mock.patch.object(launcher.subprocess, "run", return_value=version):
                with self.assertRaisesRegex(ValueError, "group or other"):
                    launcher.validate(args)


class CatalogConversionTests(unittest.TestCase):
    def source_catalog(self):
        levels = [{"effort": "low", "description": "fast"},
                  {"effort": "medium", "description": "balanced"},
                  {"effort": "high", "description": "deep"},
                  {"effort": "max", "description": "maximum"}]
        return {"models": [
            {"slug": "gpt-6.1-sol", "tool_mode": "code_mode_only",
             "supports_search_tool": True, "use_responses_lite": True,
             "multi_agent_version": "v1", "display_name": "6.1 Sol",
             "prefer_websockets": True, "service_tiers": ["priority"],
             "default_reasoning_level": "high",
             "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}]},
            {"slug": NATIVE_6LUNA, "tool_mode": "code_mode_only",
             "supports_search_tool": True, "use_responses_lite": True,
             "multi_agent_version": "v1", "display_name": "6 Luna",
             "prefer_websockets": True, "upgrade": {"slug": "gpt-6.1-sol"}},
            {"slug": "gpt-5.6-sol", "tool_mode": "code_mode",
             "supports_search_tool": True, "use_responses_lite": True,
             "multi_agent_version": "v1", "display_name": "5.6 Sol",
             "prefer_websockets": True, "service_tiers": ["priority"]},
            {"slug": WORKBUDDY_ALIAS, "tool_mode": "code_mode",
             "supports_search_tool": True, "use_responses_lite": True,
             "multi_agent_version": "v1", "display_name": "5.6 Luna",
             "description": "stock", "prefer_websockets": True,
             "default_service_tier": "priority", "service_tiers": ["priority"],
             "additional_speed_tiers": ["fast"], "upgrade": {"slug": "gpt-5.6-sol"},
             "multi_agent_reasoning_effort": "high", "default_reasoning_level": "medium",
             "supported_reasoning_levels": levels},
        ]}

    def convert(self, source, output, *arguments):
        return subprocess.run([sys.executable, str(SCRIPTS / "make-routed-catalog.py"),
                               "--source", str(source), "--output", str(output), *arguments],
                              capture_output=True, text=True, timeout=30)

    def test_conversion_keeps_native_models_byte_equivalent_and_scopes_workbuddy(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            original = self.source_catalog()
            source = directory / "source.json"
            source.write_text(json.dumps(original))
            output = directory / "routed.json"
            result = self.convert(source, output, "--model", "gpt-5.6-sol",
                                  "--workbuddy-model", WORKBUDDY_ALIAS)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(WORKBUDDY_ALIAS, result.stdout)
            converted = json.loads(output.read_text())
            before = {model["slug"]: model for model in original["models"]}
            after = {model["slug"]: model for model in converted["models"]}
            for slug in ("gpt-6.1-sol", NATIVE_6LUNA):
                with self.subTest(slug=slug):
                    self.assertEqual(json.dumps(after[slug], ensure_ascii=False),
                                     json.dumps(before[slug], ensure_ascii=False))
            routed = after["gpt-5.6-sol"]
            self.assertEqual((routed["supports_search_tool"], routed["use_responses_lite"],
                              routed["multi_agent_version"]), (False, False, "v2"))
            self.assertEqual(routed["display_name"], "5.6 Sol")
            self.assertTrue(routed["prefer_websockets"])
            self.assertEqual(routed["service_tiers"], ["priority"])
            workbuddy = after[WORKBUDDY_ALIAS]
            self.assertEqual(workbuddy["multi_agent_version"], "v2")
            self.assertFalse(workbuddy["prefer_websockets"])
            self.assertEqual(workbuddy["default_reasoning_level"], "high")
            self.assertEqual([level["effort"] for level in workbuddy["supported_reasoning_levels"]],
                             ["low", "high", "max"])
            self.assertIsNone(workbuddy["default_service_tier"])
            self.assertEqual(workbuddy["service_tiers"], [])
            self.assertEqual(workbuddy["additional_speed_tiers"], [])
            self.assertIsNone(workbuddy["upgrade"])
            self.assertEqual(workbuddy["multi_agent_reasoning_effort"], "max")
            self.assertIn("WorkBuddy", workbuddy["display_name"])
            self.assertIn("WorkBuddy", workbuddy["description"])
            self.assertEqual(source.read_text(), json.dumps(original))

    def test_conversion_rejects_missing_alias_and_missing_reasoning_levels(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            original = self.source_catalog()
            source = directory / "source.json"
            source.write_text(json.dumps(original))
            result = self.convert(source, directory / "missing.json",
                                  "--workbuddy-model", "gpt-5.6-not-there")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("gpt-5.6-not-there", result.stderr)
            pruned = self.source_catalog()
            for model in pruned["models"]:
                if model["slug"] == WORKBUDDY_ALIAS:
                    model["supported_reasoning_levels"] = [
                        level for level in model["supported_reasoning_levels"]
                        if level["effort"] != "max"]
            source.write_text(json.dumps(pruned))
            result = self.convert(source, directory / "pruned.json",
                                  "--workbuddy-model", WORKBUDDY_ALIAS)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("low/high/max", result.stderr)


if __name__ == "__main__":
    unittest.main()
