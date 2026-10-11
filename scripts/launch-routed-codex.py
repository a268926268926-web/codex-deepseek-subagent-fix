#!/usr/bin/env python3
"""Launch the existing Linux desktop/CLI through a model-scoped V2 relay."""
import argparse
import importlib.util
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

VERSION = "0.162.0-alpha.17.2"


def sibling_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_routes_config(path, include_upstream=False):
    if path is None:
        return ({}, None, None) if include_upstream else ({}, None)
    path = Path(path).resolve()
    try:
        if path.stat().st_size > 64 * 1024:
            raise ValueError("Routes configuration exceeds 64 KiB")
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        raise ValueError("Routes configuration is missing or invalid") from None
    if (not isinstance(document, dict) or type(document.get("version")) is not int
            or document["version"] != 1
            or set(document) - {"version", "model_routes", "native_profile", "upstream"}):
        raise ValueError("Unsupported routes configuration")
    routes = document.get("model_routes", {})
    if not isinstance(routes, dict) or any(not isinstance(alias, str) or not isinstance(target, str)
                                           for alias, target in routes.items()):
        raise ValueError("Invalid model routes configuration")
    helper = sibling_module("routed_relay_config", "routed-v2-relay.py")
    try:
        routes = helper.parse_model_routes([alias + "=" + target for alias, target in routes.items()])
        upstream = helper.validated_upstream(document["upstream"]) if "upstream" in document else None
    except helper.RelayError as error:
        raise ValueError(str(error)) from None
    native_profile = document.get("native_profile")
    if native_profile is not None:
        if not isinstance(native_profile, str) or not native_profile.strip():
            raise ValueError("Invalid native profile path")
        native_profile = Path(native_profile).expanduser()
        if not native_profile.is_absolute():
            native_profile = path.parent / native_profile
        native_profile = native_profile.resolve()
    return (routes, native_profile, upstream) if include_upstream else (routes, native_profile)


def validate(args):
    args.model_routes, configured_profile, configured_upstream = load_routes_config(
        getattr(args, "routes_config", None), include_upstream=True)
    args.native_profile = getattr(args, "native_profile", None) or configured_profile
    args.alias = list(dict.fromkeys([*args.alias, *args.model_routes]))
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
    upstream = configured_upstream or settings.get("base_url", "")
    helper = sibling_module("routed_relay_validation", "routed-v2-relay.py")
    try:
        upstream = helper.validated_upstream(upstream)
    except helper.RelayError as error:
        raise ValueError(str(error)) from None
    if settings.get("wire_api", "responses") != "responses":
        raise ValueError("Configured provider does not use Responses")
    if (upstream.startswith("http://") or args.model_routes) and args.native_profile is None:
        raise ValueError("Aggregation requires a private native_profile for original-provider task recovery")
    if args.native_profile is not None:
        args.native_profile = Path(args.native_profile).resolve()
        native = sibling_module("routed_native_profile", "recover-v2-tasks.py")
        native.load_native_profile(args.native_profile)
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
    flags = []
    for override in overrides:
        flags += ["-c", override]
    # On 0.162, a global -c before app-server is lost when Desktop passes its
    # own -c after the subcommand. Keep our values in the same argument scope.
    # Desktop may itself prefix app-server with -c features.code_mode_host=true.
    return ('#!/bin/sh\nunset CODEX_V2_PLAINTEXT_MESSAGES CODEX_V2_RECOVERY_MAP\n'
            'v2_command_name=\nv2_value_pending=0\n'
            'for v2_argument in "$@"; do\n'
            '  if [ "$v2_value_pending" = 1 ]; then v2_value_pending=0; continue; fi\n'
            '  case "$v2_argument" in\n'
            '    -c|--config|--enable|--disable|-m|--model|-p|--profile|-s|--sandbox|-a|--ask-for-approval|-C|--cd) v2_value_pending=1 ;;\n'
            '    --) break ;;\n'
            '    -*) ;;\n'
            '    *) v2_command_name=$v2_argument; break ;;\n'
            '  esac\ndone\n'
            'if [ "$v2_command_name" = app-server ]; then\n'
            '  exec ' + shlex.quote(str(original)) + ' "$@" ' + shlex.join(flags) + '\n'
            'fi\nexec ' + shlex.join([str(original), *flags]) + ' "$@"\n')


def stop_owned(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def verify_desktop_config(wrapper, provider, url, catalog):
    spec = importlib.util.spec_from_file_location(
        "desktop_config_check", Path(__file__).with_name("verify-desktop-wrapper.py"))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    checks = helper.read_config([str(wrapper), *helper.desktop_arguments()], provider, url, catalog)
    if not all(checks.values()):
        failed = ", ".join(key for key, passed in checks.items() if not passed)
        print("桌面有效配置核验未通过：" + failed, file=sys.stderr)
        raise RuntimeError("Desktop route verification failed")
    return checks


def main():
    directory = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-original", type=Path, default=Path("/usr/lib/chatgpt/resources/codex"))
    parser.add_argument("--app", type=Path, default=Path("/usr/lib/chatgpt/ChatGPT"))
    parser.add_argument("--catalog", type=Path, default=directory / "routed-models.json")
    parser.add_argument("--recovery-map", type=Path, default=directory / "recovered-messages.local.json")
    parser.add_argument("--routes-config", type=Path,
                        help="Model routes and native recovery profile; defaults to routed-providers.local.json if present")
    parser.add_argument("--native-profile", type=Path,
                        help="Override the independent original-provider recovery profile")
    parser.add_argument("--alias", action="append")
    parser.add_argument("--check", action="store_true", help="Validate only; do not start the desktop or relay")
    parser.add_argument("--cli", action="store_true", help="Run the original CLI instead of the desktop")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    args.alias = args.alias or ["gpt-5.6-sol"]
    if args.routes_config is None:
        candidate = directory / "routed-providers.local.json"
        args.routes_config = candidate if candidate.exists() else None
    else:
        args.routes_config = args.routes_config.resolve()
    args.arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    for name in ("codex_original", "app", "catalog", "recovery_map"):
        setattr(args, name, getattr(args, name).resolve())
    provider, upstream, cached = validate(args)
    if args.check:
        print(json.dumps({"status": "READY", "runtime": VERSION, "provider": provider,
                          "mapped_aliases": args.alias, "cached_tasks": cached,
                          "model_routes": args.model_routes,
                          "independent_native_recovery": args.native_profile is not None,
                          "desktop_activated": False}, ensure_ascii=False))
        return 0
    if not args.cli and desktop_running(args.app):
        print("请先正常退出当前 Codex，再运行此启动器。当前会话和文件不会被关闭或修改。", file=sys.stderr)
        return 2
    if upstream.startswith("http://") and any(
            target.startswith("ccs-") for target in args.model_routes.values()):
        ccs = sibling_module("routed_ccs_ready", "ensure-ccs-proxy.py")
        try:
            readiness = ccs.ensure_ccs_ready(upstream)
        except ccs.CcsReadyError as error:
            print("CC Switch 代理未就绪：" + str(error), file=sys.stderr)
            return 2
        if readiness["started"]:
            print("已启动 CC Switch，聚合代理已就绪。", file=sys.stderr)
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
        for alias, target in args.model_routes.items():
            command += ["--model-route", alias + "=" + target]
        if args.native_profile is not None:
            command += ["--native-profile", str(args.native_profile)]
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
            if not args.cli:
                verify_desktop_config(wrapper, provider, url, args.catalog)
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
