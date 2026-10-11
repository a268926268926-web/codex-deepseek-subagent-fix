#!/usr/bin/env python3
import copy
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
import urllib.request

spec = importlib.util.spec_from_file_location("relay", Path(__file__).with_name("routed-v2-relay.py"))
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)
helper_spec = importlib.util.spec_from_file_location("recovery", Path(__file__).with_name("recover-v2-tasks.py"))
recovery_helper = importlib.util.module_from_spec(helper_spec)
helper_spec.loader.exec_module(recovery_helper)


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
        relay.annotate_response(added)
        relay.annotate_response(completed)
        self.assertEqual(added["item"]["encrypted_function_args"], [])
        self.assertEqual(completed["response"]["output"][0]["encrypted_function_args"], [])
        self.assertEqual(added["item"]["id"], item["id"])
        self.assertEqual(added["item"]["arguments"], item["arguments"])

    def test_ciphertext_and_external_mcp_are_not_relabelled(self):
        opaque = {"type": "function_call", "name": "send_message",
                  "arguments": json.dumps({"message": "gAAAAABopaque"})}
        with self.assertRaises(relay.RelayError):
            relay.annotate_response(opaque)
        self.assertNotIn("encrypted_function_args", opaque)
        external = {"type": "function_call", "namespace": "mcp__example", "name": "send_message",
                    "arguments": "{}"}
        original = copy.deepcopy(external)
        relay.annotate_response(external)
        self.assertEqual(external, original)
        opaque["arguments"] = "{}"
        opaque["encrypted_function_args"] = ["message"]
        with self.assertRaises(relay.RelayError):
            relay.annotate_response(opaque)


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
        self.assertEqual(response_item["encrypted_function_args"], [])
        self.assertEqual(response_item["arguments"], item["arguments"])
        self.assertIn(b"data: [DONE]", received)

    def test_mapped_gzip_plaintext_response_is_consistently_decoded(self):
        self.upstream.reply = gzip.compress(b"Plain HTTP diagnostic"), "text/plain", "gzip"
        received, headers = self.request(b'{"model":"mapped-test","input":[]}')
        self.assertEqual(received, b"Plain HTTP diagnostic")
        self.assertNotIn("Content-Encoding", headers)

    def test_mapped_gzip_sse_response_is_decoded_and_adapted(self):
        item = {"type": "function_call", "id": "fc-gzip", "name": "send_message",
                "namespace": "collaboration", "arguments": '{"message":"Readable task"}'}
        event = {"type": "response.output_item.done", "item": item}
        stream = b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"
        self.upstream.reply = gzip.compress(stream), "text/event-stream", "gzip"
        received, headers = self.request(b'{"model":"mapped-test","input":[]}')
        self.assertEqual(json.loads(received.splitlines()[0][6:])["item"]["encrypted_function_args"], [])
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
