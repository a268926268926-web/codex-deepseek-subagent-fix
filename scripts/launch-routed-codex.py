#!/usr/bin/env python3
"""Launch the existing Linux desktop/CLI through a model-scoped V2 relay."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.parse

VERSION = "0.162.0-alpha.17.2"


def validate(args):
    for path in (args.codex_original, args.app):
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError(f"Executable unavailable: {path}")
    result = subprocess.run([str(args.codex_original), "--version"], check=True,
                            capture_output=True, text=True, timeout=10)
    if result.stdout.strip() != f"codex-cli {VERSION}":
        raise ValueError(f"This package is verified for codex-cli {VERSION}; runtime changed")
    config_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    config = tomllib.loads((config_home / "config.toml").read_text())
    provider = config.get("model_provider", "openai")
    if not isinstance(provider, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", provider):
        raise ValueError("CLI dotted overrides need a provider ID containing letters, digits, _ or -")
    settings = config.get("model_providers", {}).get(provider, {})
    upstream = settings.get("base_url", "")
    parsed = urllib.parse.urlsplit(upstream)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Configured provider needs a plain HTTPS Responses base_url")
    if settings.get("wire_api", "responses") != "responses":
        raise ValueError("Configured provider does not use Responses")
    catalog = json.loads(args.catalog.read_text())
    for alias in args.alias:
        matches = [model for model in catalog["models"] if model["slug"] == alias]
        if (len(matches) != 1
                or matches[0].get("tool_mode") not in {"code_mode", "code_mode_only"}
                or matches[0].get("multi_agent_version") != "v2"):
            raise ValueError(f"Catalog needs one Code Mode V2 entry for {alias}")
    cached = 0
    if args.recovery_map.exists():
        if args.recovery_map.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("Recovery map exceeds 4 MiB")
        mapping = json.loads(args.recovery_map.read_text())
        if mapping.get("version") != 1 or not isinstance(mapping.get("messages"), dict):
            raise ValueError("Invalid private recovery map")
        cached = len(mapping["messages"])
    return provider, upstream, cached


def desktop_running(app):
    """Detect the main Linux Electron process, excluding renderer/helper children."""
    for path in Path("/proc").iterdir():
        if not path.name.isdecimal():
            continue
        try:
            arguments = (path / "cmdline").read_bytes().split(b"\0")
            if arguments[0] and Path(os.fsdecode(arguments[0])).resolve() == app:
                if not any(arg.startswith(b"--type=") for arg in arguments):
                    return True
        except (OSError, ValueError):
            continue
    return False


def wrapper_text(original, provider, url, catalog):
    overrides = [f"model_providers.{provider}.base_url={json.dumps(url)}",
                 f"model_catalog_json={json.dumps(str(catalog))}",
                 "features.code_mode_host=true",
                 "features.multi_agent=true", "features.multi_agent_v2.enabled=true",
                 "features.multi_agent_v2.hide_spawn_agent_metadata=false",
                 "features.multi_agent_v2.expose_spawn_agent_model_overrides=true",
                 "features.multi_agent_v2.wait_agent_enabled=true"]
    command = [str(original)]
    for override in overrides:
        command += ["-c", override]
    return ('#!/bin/sh\nunset CODEX_V2_PLAINTEXT_MESSAGES CODEX_V2_RECOVERY_MAP\nexec '
            + shlex.join(command) + ' "$@"\n')


def stop_owned(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    directory = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-original", type=Path, default=Path("/usr/lib/chatgpt/resources/codex"))
    parser.add_argument("--app", type=Path, default=Path("/usr/lib/chatgpt/ChatGPT"))
    parser.add_argument("--catalog", type=Path, default=directory / "routed-models.json")
    parser.add_argument("--recovery-map", type=Path, default=directory / "recovered-messages.local.json")
    parser.add_argument("--alias", action="append")
    parser.add_argument("--check", action="store_true", help="Validate only; do not start the desktop or relay")
    parser.add_argument("--cli", action="store_true", help="Run the original CLI instead of the desktop")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    args.alias = args.alias or ["gpt-5.6-sol"]
    args.arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    for name in ("codex_original", "app", "catalog", "recovery_map"):
        setattr(args, name, getattr(args, name).resolve())
    provider, upstream, cached = validate(args)
    if args.check:
        print(json.dumps({"status": "READY", "runtime": VERSION, "provider": provider,
                          "mapped_aliases": args.alias, "cached_tasks": cached,
                          "desktop_activated": False}, ensure_ascii=False))
        return 0
    if not args.cli and desktop_running(args.app):
        print("请先正常退出当前 Codex，再运行此启动器。当前会话和文件不会被关闭或修改。", file=sys.stderr)
        return 2
    relay = client = None
    env = os.environ.copy()
    env.pop("CODEX_V2_PLAINTEXT_MESSAGES", None)
    env.pop("CODEX_V2_RECOVERY_MAP", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with tempfile.TemporaryDirectory(prefix=".codex-v2-runtime-", dir=directory) as temporary:
        runtime = Path(temporary)
        ready = runtime / "ready.json"
        command = [sys.executable, str(directory / "routed-v2-relay.py"),
                   "--upstream", upstream, "--recovery-map", str(args.recovery_map),
                   "--codex-original", str(args.codex_original),
                   "--auto-recover", "--ready-file", str(ready)]
        for alias in args.alias:
            command += ["--alias", alias]
        try:
            relay = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 10
            while not ready.exists():
                if relay.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Local V2 relay did not become ready")
                time.sleep(.05)
            url = json.loads(ready.read_text())["url"]
            wrapper = runtime / "routed-codex-cli.sh"
            wrapper.write_text(wrapper_text(args.codex_original, provider, url, args.catalog))
            wrapper.chmod(0o700)
            env["CODEX_CLI_PATH"] = str(wrapper)
            executable = wrapper if args.cli else args.app
            client = subprocess.Popen([str(executable), *args.arguments], env=env)
            return client.wait()
        finally:
            stop_owned(client)
            stop_owned(relay)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        print("启动失败：请运行 --check 检查版本、配置、目录和恢复缓存。", file=sys.stderr)
        sys.exit(1)
