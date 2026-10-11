#!/usr/bin/env python3
"""Read user-owned V2 task text through a compatible service, without editing histories."""
import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import tempfile
import time
import tomllib


def collect_tasks(paths):
    tasks = []
    seen = set()
    for path in paths:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                candidates = ([row["payload"]] if row.get("type") == "response_item" else
                              row.get("payload", {}).get("replacement_history", [])
                              if row.get("type") == "compacted" else [])
                for item in candidates:
                    if item.get("type") != "agent_message":
                        continue
                    encrypted = [part["encrypted_content"] for part in item.get("content", [])
                                 if part.get("type") == "encrypted_content"]
                    if not encrypted:
                        continue
                    if len(encrypted) != 1:
                        raise ValueError("A task has multiple encrypted parts; recover it separately")
                    digest = hashlib.sha256(encrypted[0].encode()).hexdigest()
                    if digest not in seen:
                        seen.add(digest)
                        tasks.append((digest, item))
    return tasks


def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".v2-recovery-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def disabled_integrations():
    source = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    config = tomllib.loads(source.read_text()) if source.exists() else {}
    overrides = {"features.fast_mode": False, "features.multi_agent": False,
                 "features.multi_agent_v2.enabled": False, "web_search": "disabled"}
    for category in ["mcp_servers", "plugins"]:
        settings = config.get(category, {})
        overrides[category] = {
            name: {**value, "enabled": False} if isinstance(value, dict) else value
            for name, value in settings.items()
        }
    return overrides


def read_once(args, tasks):
    command = [str(args.codex_original), "-c", 'approval_policy="never"', "app-server"]
    if args.catalog:
        command[1:1] = ["-c", "model_catalog_json=" + json.dumps(str(args.catalog))]
    for override in args.config:
        command[1:1] = ["-c", override]
    env = os.environ.copy()
    env.pop("CODEX_V2_PLAINTEXT_MESSAGES", None)
    env.pop("CODEX_V2_RECOVERY_MAP", None)
    deadline = time.monotonic() + args.timeout
    pending = collections.deque()
    buffered = b""
    with tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=stderr, env=env, bufsize=0)

        def send(value):
            process.stdin.write((json.dumps(value) + "\n").encode())
            process.stdin.flush()

        def receive():
            nonlocal buffered
            while time.monotonic() < deadline:
                if pending:
                    event = pending.popleft()
                    if event.get("method") == "item/started":
                        kind = event.get("params", {}).get("item", {}).get("type")
                        if kind not in ["userMessage", "agentMessage", "reasoning", "contextCompaction"]:
                            raise RuntimeError(f"Unexpected recovery item type {kind}; no map was accepted")
                    return event
                if process.poll() is not None:
                    raise RuntimeError("Recovery app-server exited before completion")
                readable, _, _ = select.select([process.stdout], [], [], .5)
                if readable:
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        raise RuntimeError("Recovery app-server closed its output")
                    buffered += chunk
                    while b"\n" in buffered:
                        line, buffered = buffered.split(b"\n", 1)
                        if line:
                            pending.append(json.loads(line))
            raise TimeoutError("Recovery exceeded its bounded timeout")

        def rpc(identifier, method, params):
            send({"id": identifier, "method": method, "params": params})
            while True:
                event = receive()
                if event.get("id") == identifier:
                    if "error" in event:
                        message = re.sub(r"gAAAA[A-Za-z0-9_=-]+", "[ciphertext]",
                                         event["error"].get("message", "unknown error"))[:300]
                        raise RuntimeError(f"Recovery RPC {method} failed: {message}")
                    return event["result"]

        try:
            rpc(1, "initialize", {"clientInfo": {"name": "codex_cli_rs", "version": "0.162.0-alpha.17.2"},
                                  "capabilities": {"experimentalApi": True}})
            send({"method": "initialized", "params": {}})
            started = rpc(2, "thread/start", {"model": args.model, "ephemeral": True,
                          "cwd": str(args.output.parent), "approvalPolicy": "never",
                          "sandbox": "read-only", "config": disabled_integrations()})
            thread_id = started["thread"]["id"]
            rpc(3, "thread/inject_items", {"threadId": thread_id, "items": [item for _, item in tasks]})
            prompt = (
                "Only recover the USER TASK COMMUNICATIONS in the provided agent_message items. "
                "Do not execute any task, use tools, write files, or reveal hidden reasoning. "
                "Return each task payload exactly as readable through this service, retaining all "
                "sentences, order, punctuation, spaces and newlines. Do not infer from context or "
                "paraphrase. If unavailable, return {\"status\":\"UNREADABLE\"}. Otherwise return "
                "only JSON: {\"status\":\"RECOVERED_TASK_TEXT\",\"messages\":[{\"payload\":\"...\"}]}, "
                "with messages in the same order as the provided items."
            )
            rpc(4, "turn/start", {"threadId": thread_id, "effort": args.effort,
                                 "input": [{"type": "text", "text": prompt, "textElements": []}]})
            texts = []
            while True:
                event = receive()
                params = event.get("params", {})
                if event.get("method") == "item/completed" and params.get("item", {}).get("type") == "agentMessage":
                    texts.append(params["item"].get("text", ""))
                if event.get("method") == "turn/completed" and params.get("threadId") == thread_id:
                    if params.get("turn", {}).get("status") != "completed":
                        raise RuntimeError("Compatible service could not finish recovery")
                    break
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", texts[-1].strip()) if texts else ""
            result = json.loads(text)
            if result.get("status") != "RECOVERED_TASK_TEXT" or len(result.get("messages", [])) != len(tasks):
                raise ValueError("Service did not recover every requested task")
            payloads = [message["payload"] for message in result["messages"]]
            if any(not isinstance(s, str) or not s.strip() or s.lstrip().startswith("gAAAAA") for s in payloads):
                raise ValueError("Service returned empty or opaque task content")
            return payloads
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollout", required=True, action="append", type=Path)
    parser.add_argument("--codex-original", required=True, type=Path)
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--effort", default="low")
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--config", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.codex_original = args.codex_original.resolve()
    if args.catalog:
        args.catalog = args.catalog.resolve()
    if args.output.exists():
        raise ValueError("Output already exists; use a new filename")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tasks = collect_tasks(args.rollout)
    if not tasks:
        raise ValueError("No encrypted agent task items were found")
    first = read_once(args, tasks)
    second = read_once(args, tasks)
    if first != second:
        raise ValueError("Independent recovery texts differ; no automatic map was written")
    mapping = {digest: text for (digest, _), text in zip(tasks, first)}
    private_json(args.output, {"version": 1, "messages": mapping})
    private_json(args.output.with_suffix(args.output.suffix + ".evidence.json"), {
        "method": "model-mediated compatible-service readback, not local decryption",
        "model": args.model, "task_count": len(tasks), "matching_reads": 2,
        "source_files": [str(path.resolve()) for path in args.rollout],
        "source_sha256": [hashlib.sha256(path.read_bytes()).hexdigest() for path in args.rollout],
    })
    print(f"Recovered {len(tasks)} task texts; two matching reads. Private map: {args.output}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Recovery failed: {error}. No usable automatic map was accepted.", file=sys.stderr)
        sys.exit(1)
