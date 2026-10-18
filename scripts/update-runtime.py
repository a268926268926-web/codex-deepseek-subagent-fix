#!/usr/bin/env python3
"""Validate an installed Codex update locally; never install software or call a model."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib


def load_launcher():
    spec = importlib.util.spec_from_file_location(
        "runtime_update_launcher", Path(__file__).with_name("launch-routed-codex.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record_verified_runtime(path, version, checks, fingerprint):
    launcher = load_launcher()
    if checks != dict.fromkeys(launcher.REQUIRED_RUNTIME_CHECKS, True):
        raise RuntimeError("Desktop compatibility checks did not all pass")
    data = {"schema": 1, "runtime": version, "checks": checks, "sha256": fingerprint,
            "verified_at_utc": datetime.now(timezone.utc).isoformat(),
            "validation_scope": "local_configuration",
            "model_requests_made": False, "subagents_tested": False}
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent,
                                         prefix=".runtime-check-", delete=False) as stream:
            temp = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    return data


def main():
    directory = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-original", type=Path,
                        default=Path("/usr/lib/chatgpt/resources/codex"))
    parser.add_argument("--catalog", type=Path, default=directory / "routed-models.json")
    args = parser.parse_args()
    args.codex_original = args.codex_original.resolve()
    args.catalog = args.catalog.resolve()
    launcher = load_launcher()
    version = launcher.runtime_version(args.codex_original)
    if version == "unrecognized":
        raise RuntimeError("Unrecognized CLI version output")
    home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    config = tomllib.loads((home / "config.toml").read_text())
    provider = config.get("model_provider", "openai")
    if not isinstance(provider, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", provider):
        raise RuntimeError("Unsupported provider ID")
    fingerprint = launcher.runtime_fingerprint(args.codex_original, args.catalog)
    verifier = launcher.sibling_module("runtime_update_verifier", "verify-desktop-wrapper.py")
    with tempfile.TemporaryDirectory(prefix="codex-v2-update-check-") as temporary:
        wrapper = Path(temporary) / "codex-wrapper.sh"
        url = "http://127.0.0.1:9"
        wrapper.write_text(launcher.wrapper_text(args.codex_original, provider, url, args.catalog))
        wrapper.chmod(0o700)
        checks = verifier.read_config([str(wrapper), *verifier.desktop_arguments()],
                                      provider, url, args.catalog)
    if fingerprint != launcher.runtime_fingerprint(args.codex_original, args.catalog):
        raise RuntimeError("Runtime or validation inputs changed during the check")
    record_verified_runtime(directory / "runtime-compatibility.local.json",
                            version, checks, fingerprint)
    print(json.dumps({"status": "READY", "runtime": version, "checks": checks,
                      "validation_scope": "local_configuration",
                      "model_requests_made": False, "subagents_tested": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # Config and child-process errors can contain credentials; expose only their type.
        print(json.dumps({"status": "BLOCKED", "error_type": type(error).__name__,
                          "message": "本地兼容性验证失败，未启用新的运行时。"}, ensure_ascii=False))
        raise SystemExit(1)
