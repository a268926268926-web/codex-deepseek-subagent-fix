#!/usr/bin/env python3
"""Keep native Responses traffic intact; adapt V2 tasks for explicitly routed aliases."""
import argparse
import copy
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import ipaddress
import json
from pathlib import Path
import re
import sys
import threading
from types import SimpleNamespace
import urllib.error
import urllib.parse
import urllib.request

sys.dont_write_bytecode = True

MESSAGE_TOOLS = {"spawn_agent", "followup_task", "send_message"}
HOP_HEADERS = {"host", "content-length", "connection", "transfer-encoding", "keep-alive",
               "proxy-authorization", "proxy-authenticate", "upgrade"}
MAX_BODY = 32 * 1024 * 1024


class RelayError(Exception):
    pass


def validated_upstream(url):
    """HTTPS services and an explicitly local HTTP aggregation service are supported."""
    if not isinstance(url, str) or any(character.isspace() for character in url) or "\\" in url:
        raise RelayError("Upstream must be a plain HTTPS or loopback HTTP base_url")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        raise RelayError("Upstream must be a plain HTTPS or loopback HTTP base_url") from None
    if not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise RelayError("Upstream must be a plain HTTPS or loopback HTTP base_url")
    local = parsed.hostname.lower() == "localhost"
    if not local:
        try:
            local = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            local = False
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise RelayError("HTTP upstream must be loopback; remote services require HTTPS")
    return url.rstrip("/")


def parse_model_routes(specifications):
    routes = {}
    for specification in specifications:
        if not isinstance(specification, str) or specification.count("=") != 1:
            raise RelayError("Model routes must use alias=target")
        alias, target = specification.split("=", 1)
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}", alias)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,511}", target)):
            raise RelayError("Invalid model route alias or target")
        if alias in routes:
            raise RelayError("Duplicate model route alias")
        routes[alias] = target
    return routes


def bounded_decompress(body):
    with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
        decoded = stream.read(MAX_BODY + 1)
    if len(decoded) > MAX_BODY:
        raise RelayError("Decompressed request exceeds relay limit")
    return decoded


def guard_recovery_response(value):
    """Never forward a tool-call item to a recovery CLI, even from a faulty upstream."""
    allowed = {"message", "agent_message", "reasoning", "compaction", "context_compaction"}
    if isinstance(value, dict):
        item = value.get("item")
        output = value.get("output")
        candidates = ([item] if isinstance(item, dict) else [])
        if isinstance(output, list):
            candidates += [part for part in output if isinstance(part, dict)]
        if any(item.get("type") not in allowed for item in candidates):
            raise RelayError("Recovery tool call blocked before local dispatch")
        if value.get("type", "").startswith(("response.function_call", "response.custom_tool_call")):
            raise RelayError("Recovery tool call blocked before local dispatch")
        for part in value.values():
            if isinstance(part, (dict, list)):
                guard_recovery_response(part)
    elif isinstance(value, list):
        for part in value:
            guard_recovery_response(part)


def message_tool(item):
    name = item.get("name", "")
    namespace = item.get("namespace") or (name.rsplit(".", 1)[0] if "." in name else "functions")
    return (item.get("type") == "function_call" and name.split(".")[-1] in MESSAGE_TOOLS
            and namespace in {"functions", "collaboration"})


def annotate_response(value):
    """Upstream Codex recognizes an explicit empty list as direct plaintext V2 arguments."""
    if isinstance(value, list):
        for item in value:
            annotate_response(item)
    elif isinstance(value, dict):
        if message_tool(value):
            if value.get("encrypted_function_args"):
                raise RelayError("Routed model returned encrypted tool arguments")
            arguments = value.get("arguments", "")
            if arguments:
                try:
                    parameters = json.loads(arguments)
                except (ValueError, TypeError):
                    parameters = {}
                text = parameters.get("message", "") if isinstance(parameters, dict) else ""
                if isinstance(text, str) and text.lstrip().startswith("gAAAAA"):
                    raise RelayError("Routed model returned opaque task content")
            value["encrypted_function_args"] = []
        for child in value.values():
            if isinstance(child, (dict, list)):
                annotate_response(child)


def clear_message_markers(value, namespace="functions"):
    if isinstance(value, list):
        for child in value:
            clear_message_markers(child, namespace)
    elif isinstance(value, dict):
        if value.get("type") == "namespace":
            namespace = value.get("name", namespace)
        if namespace in {"functions", "collaboration"} and value.get("name", "").split(".")[-1] in MESSAGE_TOOLS:
            schema = value.get("parameters", {}).get("properties", {}).get("message")
            if isinstance(schema, dict):
                schema.pop("encrypted", None)
        for child in value.values():
            if isinstance(child, (dict, list)):
                clear_message_markers(child, namespace)


class TaskRecovery:
    def __init__(self, args):
        self.args = args
        self.lock = threading.Lock()
        self.task_locks = {}
        self.messages = {}
        if args.recovery_map.exists():
            if args.recovery_map.stat().st_size > 4 * 1024 * 1024:
                raise RelayError("Recovery map exceeds 4 MiB")
            document = json.loads(args.recovery_map.read_text())
            if not isinstance(document, dict) or document.get("version") != 1:
                raise RelayError("Unsupported recovery map version")
            self.messages = document.get("messages")
            if not isinstance(self.messages, dict):
                raise RelayError("Invalid recovery map document")
            if any(not re.fullmatch("[0-9a-f]{64}", digest) or not isinstance(text, str)
                   or not text.strip() or text.lstrip().startswith("gAAAAA")
                   for digest, text in self.messages.items()):
                raise RelayError("Invalid recovery map entry")
        path = Path(__file__).with_name("recover-v2-tasks.py")
        spec = importlib.util.spec_from_file_location("v2_task_recovery", path)
        self.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.helper)
        native_profile = getattr(args, "native_profile", None)
        if native_profile is not None:
            self.helper.load_native_profile(native_profile)

    def read(self, cipher, item):
        self.helper.communication_kind(item)
        digest = hashlib.sha256(cipher.encode()).hexdigest()
        with self.lock:
            if digest in self.messages:
                return self.messages[digest]
            task_lock = self.task_locks.setdefault(digest, threading.Lock())
        if not self.args.auto_recover:
            raise RelayError("Encrypted task has no local recovery entry")
        with task_lock:
            with self.lock:
                if digest in self.messages:
                    return self.messages[digest]
            options = SimpleNamespace(codex_original=self.args.codex_original,
                                      catalog=None, config=[],
                                      model=self.args.recovery_model, effort="low",
                                      output=self.args.recovery_map, timeout=self.args.timeout,
                                      native_profile=getattr(self.args, "native_profile", None))
            # Native readback must use the original runtime's built-in catalog.
            # The routed catalog is only for the desktop/parent invocation.
            # Submit only the particular encrypted task, retaining its original item ID.
            probe = copy.deepcopy(item)
            probe["content"] = [part for part in probe["content"]
                                if part.get("type") != "encrypted_content"
                                or part.get("encrypted_content") == cipher]
            self.helper.communication_kind(probe)
            first = self.helper.read_once(options, [(digest, probe)])
            second = self.helper.read_once(options, [(digest, probe)])
            if first != second:
                raise RelayError("Independent task readbacks differ")
            with self.lock:
                updated = {**self.messages, digest: first[0]}
                self.helper.private_json(self.args.recovery_map,
                                         {"version": 1, "messages": updated})
                self.messages = updated
            return first[0]


def adapt_request(value, recovery):
    items = value.get("input")
    if isinstance(items, list):
        rewritten = []
        for item in items:
            if not isinstance(item, dict) or item.get("type") != "agent_message":
                rewritten.append(item)
                continue
            parts = []
            for part in item.get("content", []):
                if part.get("type") == "input_text":
                    parts.append(part["text"])
                elif part.get("type") == "encrypted_content":
                    parts.append(recovery.read(part["encrypted_content"], item))
                else:
                    raise RelayError("Unsupported agent task part")
            if not parts or not "\n".join(parts).strip():
                raise RelayError("Empty agent task")
            restored = {"type": "message", "role": "user",
                        "content": [{"type": "input_text", "text": "\n".join(parts)}]}
            if item.get("id"):
                restored["id"] = item["id"]
            rewritten.append(restored)
        value["input"] = rewritten
    clear_message_markers(value.get("tools", []))
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # Never log incoming headers, URL queries, task text or raw responses.

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def fail(self, message, status=502):
        body = json.dumps({"error": {"message": message, "type": "v2_relay_error"}}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def forward(self):
        headers_sent = False
        upstream = None
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > MAX_BODY:
                raise RelayError("Request size exceeds relay limit")
            body = self.rfile.read(size) if size else None
            mapped = False
            guarded = getattr(self.server, "recovery_guard", False)
            if body and "application/json" in self.headers.get("Content-Type", ""):
                decoded = bounded_decompress(body) if self.headers.get("Content-Encoding", "").lower() == "gzip" else body
                try:
                    value = json.loads(decoded)
                except ValueError:
                    value = None
                model = value.get("model") if isinstance(value, dict) else None
                routes = getattr(self.server, "model_routes", {})
                mapped = (not guarded and isinstance(model, str)
                          and (model in self.server.aliases or model in routes))
                if guarded:
                    if not isinstance(value, dict):
                        raise RelayError("Recovery request must be a JSON object")
                    value["tools"] = []
                    value["tool_choice"] = "none"
                    for item in value.get("input", []):
                        if isinstance(item, dict) and item.get("type") == "additional_tools":
                            item["tools"] = []
                elif mapped:
                    value = adapt_request(value, self.server.recovery)
                    if model in routes:
                        value["model"] = routes[model]
                if mapped or guarded:
                    body = json.dumps(value,
                                      ensure_ascii=False, separators=(",", ":")).encode()
            headers = {key: value for key, value in self.headers.items()
                       if key.lower() not in HOP_HEADERS}
            inspected = mapped or guarded
            if inspected:
                headers = {key: value for key, value in headers.items() if key.lower() != "content-encoding"}
                headers["Accept-Encoding"] = "identity"
            request = urllib.request.Request(self.server.upstream + self.path,
                                             data=body, headers=headers, method=self.command)
            try:
                upstream = self.server.opener.open(request, timeout=self.server.timeout)
            except urllib.error.HTTPError as error:
                upstream = error
            if guarded and upstream.status < 400 and not any(
                    media in upstream.headers.get("Content-Type", "")
                    for media in ("application/json", "text/event-stream")):
                raise RelayError("Unsupported recovery response format")
            stream = gzip.GzipFile(fileobj=upstream) if inspected and upstream.headers.get("Content-Encoding", "").lower() == "gzip" else upstream
            json_body = None
            if inspected and "application/json" in upstream.headers.get("Content-Type", ""):
                raw = stream.read(MAX_BODY + 1)
                if len(raw) > MAX_BODY:
                    raise RelayError("JSON response exceeds relay limit")
                value = json.loads(raw)
                if guarded:
                    guard_recovery_response(value)
                if mapped:
                    annotate_response(value)
                json_body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(upstream.status)
            for key, value in upstream.headers.items():
                if key.lower() not in HOP_HEADERS and (not inspected or key.lower() != "content-encoding"):
                    self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            headers_sent = True
            if inspected and "text/event-stream" in upstream.headers.get("Content-Type", ""):
                pending_args = {}
                message_ids = set()
                while line := stream.readline():
                    if line.startswith(b"data:") and line[5:].strip() != b"[DONE]":
                        value = json.loads(line[5:])
                        if guarded:
                            guard_recovery_response(value)
                        item = value.get("item", {})
                        if message_tool(item) and item.get("id"):
                            message_ids.add(item["id"])
                        if value.get("type") == "response.function_call_arguments.delta" and value.get("item_id") in message_ids:
                            identifier = value["item_id"]
                            args = pending_args.get(identifier, "") + value.get("delta", "")
                            pending_args[identifier] = args
                            if re.search(r'"message"\s*:\s*"\s*gAAAAA', args):
                                raise RelayError("Routed model returned opaque task content")
                        if mapped:
                            annotate_response(value)
                        line = b"data: " + json.dumps(value, ensure_ascii=False).encode() + b"\n"
                    self.wfile.write(line)
                    self.wfile.flush()
            elif json_body is not None:
                self.wfile.write(json_body)
            else:
                while chunk := stream.read1(65536):
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except RelayError as error:
            if not headers_sent:
                self.fail(str(error))
            else:
                event = {"type": "error", "error": {"message": str(error), "type": "v2_relay_error"}}
                self.wfile.write(b"event: error\ndata: " + json.dumps(event).encode() + b"\n\n")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            if not headers_sent:
                self.fail("V2 relay or compatible-service recovery failed")
        finally:
            if upstream is not None:
                upstream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--alias", action="append", default=[])
    parser.add_argument("--model-route", action="append", default=[], metavar="ALIAS=TARGET",
                        help="Rewrite an explicit mapped alias before forwarding Responses to an aggregator")
    parser.add_argument("--recovery-map", type=Path, required=True)
    parser.add_argument("--codex-original", type=Path, required=True)
    parser.add_argument("--native-profile", type=Path,
                        help="Private original-provider credentials for independent encrypted-task recovery")
    parser.add_argument("--recovery-model", default="gpt-6.1-sol")
    parser.add_argument("--auto-recover", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    try:
        args.upstream = validated_upstream(args.upstream)
        routes = parse_model_routes(args.model_route)
    except RelayError as error:
        parser.error(str(error))
    if not args.alias and not routes:
        parser.error("At least one explicit routed alias is required")
    if args.auto_recover and (args.upstream.startswith("http://") or routes) and args.native_profile is None:
        parser.error("Aggregation with automatic recovery requires --native-profile for the original provider")
    args.recovery_map = args.recovery_map.resolve()
    args.codex_original = args.codex_original.resolve()
    if args.native_profile is not None:
        args.native_profile = args.native_profile.resolve()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    server.aliases = set(args.alias) | set(routes)
    server.model_routes = routes
    server.upstream = args.upstream.rstrip("/")
    server.timeout = args.timeout
    server.opener = urllib.request.build_opener(NoRedirect())
    server.recovery = TaskRecovery(args)
    server.recovery.helper.private_json(args.ready_file.resolve(),
                                       {"url": f"http://127.0.0.1:{server.server_port}"})
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
