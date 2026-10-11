#!/usr/bin/env python3
"""Verify effective app-server config with Desktop's trailing -c arguments.

No model request is made. Only nonsecret boolean checks are retained.
"""
import argparse
import collections
import importlib.util
import json
import os
from pathlib import Path
import select
import subprocess
import tempfile
import time

spec = importlib.util.spec_from_file_location("launcher", Path(__file__).with_name("launch-routed-codex.py"))
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def desktop_arguments():
    return ["-c", "features.code_mode_host=true", "app-server", "--analytics-default-enabled",
            "-c", "plugins.codex-app-tools@openai-bundled.mcp_servers.codex_app.enabled=true",
            "-c", "plugins.code-review@openai-bundled.mcp_servers.code-review.enabled=true"]


def read_config(command, provider, url, catalog):
    p = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, bufsize=0)
    queue = collections.deque()
    buffer = b""

    def rpc(identifier, method, params):
        nonlocal buffer
        p.stdin.write((json.dumps({"id": identifier, "method": method, "params": params})+"\n").encode())
        p.stdin.flush()
        end = time.monotonic()+25
        while time.monotonic() < end:
            if queue:
                response = queue.popleft()
                if response.get("id") == identifier:
                    if "error" in response:
                        raise RuntimeError(f"{method}: RPC error code {response['error'].get('code')}")
                    return response["result"]
                continue
            ready, _, _ = select.select([p.stdout], [], [], min(.5, end-time.monotonic()))
            if ready:
                data = os.read(p.stdout.fileno(), 65536)
                if not data:
                    raise RuntimeError("Owned app-server closed stdout")
                buffer += data
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if line:
                        queue.append(json.loads(line))
        raise TimeoutError("Bounded app-server config verification timed out")

    try:
        rpc(1, "initialize", {"clientInfo": {"name": "codex_desktop", "version": launcher.VERSION},
                              "capabilities": {"experimentalApi": True}})
        p.stdin.write(b'{"method":"initialized","params":{}}\n')
        p.stdin.flush()
        cfg = rpc(2, "config/read", {"includeLayers": False})["config"]
        features = cfg.get("features", {})
        v2 = features.get("multi_agent_v2", {})
        plugins = cfg.get("plugins", {})
        return {"provider_route_matches": cfg.get("model_providers", {}).get(provider, {}).get("base_url") == url,
                "catalog_matches": cfg.get("model_catalog_json") == str(catalog),
                "code_mode_host_enabled": features.get("code_mode_host") is True,
                "multi_agent_enabled": features.get("multi_agent") is True,
                "v2_enabled": isinstance(v2, dict) and v2.get("enabled") is True,
                "model_overrides_exposed": isinstance(v2, dict) and v2.get("expose_spawn_agent_model_overrides") is True,
                "wait_agent_enabled": isinstance(v2, dict) and v2.get("wait_agent_enabled") is True,
                "spawn_metadata_visible": isinstance(v2, dict) and v2.get("hide_spawn_agent_metadata") is False,
                "desktop_plugin_arguments_preserved": all(
                    plugins.get(name, {}).get("mcp_servers", {}).get(server, {}).get("enabled") is True
                    for name, server in [("codex-app-tools@openai-bundled", "codex_app"),
                                         ("code-review@openai-bundled", "code-review")])}
    finally:
        p.stdin.close()
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            launcher.stop_owned(p)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-original", type=Path, default=Path("/usr/lib/chatgpt/resources/codex"))
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--provider", default="custom")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    args.codex_original = args.codex_original.resolve()
    args.catalog = args.catalog.resolve()
    version = subprocess.run([str(args.codex_original), "--version"], capture_output=True,
                             text=True, check=True, timeout=10).stdout.strip()
    if version != f"codex-cli {launcher.VERSION}":
        raise RuntimeError("This regression targets exactly the launcher's verified runtime version")
    if not args.catalog.is_file():
        raise ValueError("A valid routed model catalog is required")
    url = "http://127.0.0.1:9"
    protected = [f"model_providers.{args.provider}.base_url={json.dumps(url)}",
                 f"model_catalog_json={json.dumps(str(args.catalog))}", "features.code_mode_host=true"]
    global_flags = [arg for value in protected for arg in ("-c", value)]
    desktop_args = desktop_arguments()
    before_plain = read_config([str(args.codex_original), *global_flags, "app-server"], args.provider, url, args.catalog)
    before_desktop = read_config([str(args.codex_original), *global_flags, *desktop_args], args.provider, url, args.catalog)
    with tempfile.TemporaryDirectory(prefix="codex-desktop-config-check-") as temporary:
        wrapper = Path(temporary) / "wrapper.sh"
        wrapper.write_text(launcher.wrapper_text(args.codex_original, args.provider, url, args.catalog))
        wrapper.chmod(0o700)
        after_desktop = read_config([str(wrapper), *desktop_args], args.provider, url, args.catalog)
    result = {"version": version, "before_plain": before_plain, "before_desktop": before_desktop,
              "after_desktop": after_desktop,
              "before_bug_reproduced": all(before_plain[k] and not before_desktop[k] for k in
                                           ("provider_route_matches", "catalog_matches", "code_mode_host_enabled")),
              "fixed_desktop_config_passed": all(after_desktop.values())}
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
        args.output.chmod(0o600)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["before_bug_reproduced"] and result["fixed_desktop_config_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
