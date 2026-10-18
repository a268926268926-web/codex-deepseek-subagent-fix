#!/usr/bin/env python3
"""Prepare an isolated, non-starting CC Switch home for WorkBuddy DeepSeek routing.

This tool never launches or signals a running CC Switch, Codex, or WorkBuddy. It
reads the source home read-only and only writes a brand-new private output tree.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import stat
import sys
import time
import tomllib
import urllib.parse

SCHEMA_VERSION = 20
WORKBUDDY_BASE_URL = "http://127.0.0.1:7863"
WORKBUDDY_MODEL = "cn:deepseek-v4.1-flash"
WORKBUDDY_ALIAS = WORKBUDDY_MODEL
WORKBUDDY_WINDOW = 1_000_000
PROVIDER_COLUMNS = {
    "id", "app_type", "name", "settings_config", "website_url", "category", "created_at",
    "sort_index", "notes", "icon", "icon_color", "meta", "is_current", "in_failover_queue",
    "cost_multiplier", "limit_daily_usd", "limit_monthly_usd", "provider_type",
}


class PrepareError(Exception):
    """Preparation failure whose message never contains credential material."""


def fail(message):
    raise PrepareError(message)


def private_dir(path, mode=0o700):
    path.mkdir(parents=True, exist_ok=False, mode=mode)
    os.chmod(path, mode)


def write_private(path, data, mode=0o600):
    if isinstance(data, str):
        data = data.encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, mode)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
    os.chmod(path, mode)


def read_private_source(path, limit=1 << 20):
    target = Path(path)
    if target.is_symlink():
        fail("source file must not be a symlink: " + target.name)
    try:
        info = target.stat()
    except OSError:
        fail("source file is missing: " + target.name)
    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > limit:
        fail("source file has an invalid type or size: " + target.name)
    return target.read_bytes()


def load_json_private(path):
    raw = read_private_source(path)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        fail("source file is not valid UTF-8 JSON: " + Path(path).name)


def load_toml_private(path):
    raw = read_private_source(path)
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        fail("source file is not valid UTF-8 TOML: " + Path(path).name)


def plain_https(url, label):
    if not isinstance(url, str) or not url or any(character.isspace() for character in url):
        fail(label + " must be a plain HTTPS URL")
    try:
        parsed = urllib.parse.urlsplit(url)
        _ = parsed.port
    except ValueError:
        fail(label + " must be a plain HTTPS URL")
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment):
        fail(label + " must be a plain HTTPS URL without userinfo, query or fragment")
    return url.rstrip("/")


def printable_secret(value, label):
    if (not isinstance(value, str) or not value or len(value) > 4096 or not value.isascii()
            or any(ord(character) < 0x21 or ord(character) > 0x7E for character in value)):
        fail(label + " must be a non-empty printable ASCII string")
    return value


def slug(text):
    """Mirror CC Switch mode::stack::allocate_key slug rules (ASCII, -, max 24, no --)."""
    out = []
    for character in text:
        lowered = character.lower()
        if ("a" <= lowered <= "z") or ("0" <= lowered <= "9"):
            out.append(lowered)
        elif out and out[-1] != "-":
            out.append("-")
        if len(out) >= 24:
            break
    return "".join(out).strip("-")


def allocate_key(keys, provider_id, preferred):
    """allocate_key: reuse a registered key, otherwise take a free slug."""
    for key, owner in keys.items():
        if owner == provider_id:
            return key
    base = slug(preferred) or "provider"
    candidate = base
    suffix = 2
    while candidate in keys and keys[candidate] != provider_id:
        candidate = base + "-" + str(suffix)
        suffix += 1
    return candidate


def pick_port(requested):
    if requested is not None:
        if not 1 <= requested <= 65535:
            fail("port must be within 1..65535")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", requested))
            except OSError:
                fail("requested port is not free on 127.0.0.1")
        return requested
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_database(source, destination):
    """Copy the live database through SQLite's read-only online backup API."""
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    source_uri = "file:" + urllib.parse.quote(str(source)) + "?mode=ro"
    connection = sqlite3.connect(source_uri, uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        target = sqlite3.connect(str(destination))
        try:
            connection.backup(target)
        finally:
            target.close()
    finally:
        connection.close()
    os.chmod(destination, 0o600)


def extract_model(config_text):
    match = re.search(r'^\s*"?model"?\s*=\s*"([^"\n]+)"', config_text, re.M)
    return match.group(1) if match else None


def extract_base_url(config_text):
    try:
        document = tomllib.loads(config_text)
    except tomllib.TOMLDecodeError:
        return None
    tables = document.get("model_providers")
    if not isinstance(tables, dict):
        return None
    chosen = document.get("model_provider")
    table = None
    if isinstance(chosen, str) and isinstance(tables.get(chosen), dict):
        table = tables[chosen]
    elif len(tables) == 1:
        table = next(iter(tables.values()))
    if not isinstance(table, dict):
        return None
    url = table.get("base_url")
    return url if isinstance(url, str) and url else None


def rewrite_card_config(config_text, old_url, new_url, api_key, auth_existing,
                        live_preferences=None):
    """Point the retained card at the effective base_url and credential path."""
    text = config_text
    if isinstance(old_url, str) and old_url and old_url != new_url:
        text = text.replace(old_url, new_url)
    if new_url not in text:
        fail("retained Codex card has no rewritable base_url")
    if live_preferences is not None:
        first_table = re.search(r"^[ \t]*\[", text, re.M)
        header = text[:first_table.start()] if first_table else text
        tail = text[first_table.start():] if first_table else ""
        for key in ("model", "model_reasoning_effort"):
            if key not in live_preferences:
                continue
            value = live_preferences[key]
            if not isinstance(value, str):
                fail("live Codex model preferences must be strings")
            line = key + " = " + json.dumps(value)
            pattern = r'^[ \t]*"?' + key + r'"?[ \t]*=[^\n]*$'
            if re.search(pattern, header, re.M):
                header = re.sub(pattern, lambda _: line, header, count=1, flags=re.M)
            else:
                header = header.rstrip("\n") + "\n" + line + "\n"
        text = header.rstrip("\n") + "\n\n" + tail
    document = tomllib.loads(text)
    tables = document.get("model_providers")
    chosen = document.get("model_provider")
    table = tables.get(chosen) if isinstance(tables, dict) and isinstance(chosen, str) else None
    if not isinstance(table, dict):
        fail("retained Codex card has no resolvable model provider table")
    if table.get("requires_openai_auth") is True:
        fail("retained Codex card requires an official login; refusing to seed it")
    # The CC Switch v4 proxy adapter's fallback scans unquoted `base_url = ...` text.
    # CC Switch's own editor may quote TOML keys; an explicit adapter URL keeps
    # the client TOML intact while making that valid card usable by the proxy.
    settings = {"auth": {}, "config": text, "base_url": new_url}
    if isinstance(auth_existing, dict):
        settings["auth"] = dict(auth_existing)
    settings["auth"]["OPENAI_API_KEY"] = api_key
    return settings


def tool_profile_text(base_url, model, window, provider_name):
    """Minimal Chat-format Codex line; provider name must match the route table."""
    return (
        'model_provider = "custom"\n'
        "model = " + json.dumps(model) + "\n"
        "model_context_window = " + str(window) + "\n"
        "model_auto_compact_token_limit = " + str(window * 9 // 10) + "\n"
        "\n[model_providers]\n"
        "\n[model_providers.custom]\n"
        "name = " + json.dumps(provider_name) + "\n"
        "base_url = " + json.dumps(base_url.rstrip("/") + "/v1") + "\n"
        "wire_api = \"responses\"\n"
        "requires_openai_auth = false\n"
    )


def isolated_client_text(config, provider, table, api_key):
    """Preserve relevant live scalars without copying unrelated integrations."""
    fields = ("model", "model_context_window", "model_auto_compact_token_limit",
              "model_reasoning_summary", "model_reasoning_effort", "disable_response_storage",
              "web_search", "sandbox_mode", "approval_policy")
    lines = ["model_provider = " + json.dumps(provider)]
    for field in fields:
        if field in config:
            value = config[field]
            if type(value) not in (str, int, float, bool):
                fail("source client routing field has an unsupported value")
            lines.append(field + " = " + json.dumps(value))
    selected = dict(table)
    for field in ("env_key", "env_key_instructions", "auth"):
        selected.pop(field, None)
    selected["requires_openai_auth"] = False
    selected["experimental_bearer_token"] = api_key
    lines += ["", "[model_providers." + json.dumps(provider) + "]"]
    for field, value in selected.items():
        if type(value) in (str, int, float, bool):
            lines.append(json.dumps(field) + " = " + json.dumps(value))
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Prepare a private, non-starting CC Switch aggregation home")
    parser.add_argument("--source-ccs-dir", type=Path, default=Path("~/.cc-switch"))
    parser.add_argument("--source-codex-dir", type=Path, default=Path("~/.codex"))
    parser.add_argument("--workbuddy-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)

    source_ccs = args.source_ccs_dir.expanduser().resolve()
    source_codex = args.source_codex_dir.expanduser().resolve()
    workbuddy_path = args.workbuddy_config.expanduser().resolve()
    output = args.output_dir.expanduser().resolve()
    if output.exists():
        fail("output directory already exists; refusing to overwrite")
    for label, directory in (("source CC Switch", source_ccs), ("source Codex", source_codex)):
        if not directory.is_dir():
            fail(label + " directory is missing")
    if any(source == output or source in output.parents for source in (source_ccs, source_codex)):
        fail("output directory must not be a source directory")

    source_db = source_ccs / "cc-switch.db"
    source_settings = source_ccs / "settings.json"
    source_live = source_ccs / "live-state.json"
    source_config = source_codex / "config.toml"
    source_auth = source_codex / "auth.json"
    for required in (source_db, source_settings, source_live, source_config):
        if not required.is_file():
            fail("required source file is missing: " + required.name)

    baseline = {name: sha256_file(path) for name, path in (
        ("cc-switch.db", source_db),
        ("settings.json", source_settings),
        ("live-state.json", source_live),
        ("codex-config.toml", source_config),
    )}

    live_config = load_toml_private(source_config)
    live_settings = load_json_private(source_settings)
    live_state = load_json_private(source_live)
    workbuddy = load_json_private(workbuddy_path)
    workbuddy_api_key = printable_secret(workbuddy.get("api_key"), "WorkBuddy api_key")

    live_auth = load_json_private(source_auth) if source_auth.is_file() else None

    client_id = live_config.get("model_provider")
    providers = live_config.get("model_providers")
    client_table = providers.get(client_id) if isinstance(providers, dict) else None
    if not isinstance(client_table, dict):
        fail("source global config has no resolvable model provider table")
    if client_table.get("wire_api", "responses") != "responses":
        fail("source global provider does not speak the Responses protocol")
    live_base_url = plain_https(client_table.get("base_url"), "source global base_url")
    provider_name = client_table.get("name")
    if not isinstance(provider_name, str) or not provider_name.strip():
        provider_name = "OpenAI"
    provider_name = re.sub(r"[^ -~]", "", provider_name).strip()[:200] or "OpenAI"

    credential_candidates = []
    bearer = client_table.get("experimental_bearer_token")
    if isinstance(bearer, str) and bearer:
        credential_candidates.append((bearer, "provider-bearer"))
    env_name = client_table.get("env_key")
    if isinstance(env_name, str) and env_name:
        env_value = os.environ.get(env_name)
        if env_value:
            credential_candidates.append((env_value, "provider-environment"))
    if isinstance(live_auth, dict) and isinstance(live_auth.get("OPENAI_API_KEY"), str):
        credential_candidates.append((live_auth["OPENAI_API_KEY"], "auth.json"))
    api_key = credential_source = None
    for candidate, origin in credential_candidates:
        if candidate:
            api_key, credential_source = printable_secret(candidate, "original credential"), origin
            break
    if api_key is None:
        fail("no original provider credential found in the source Codex home")

    port = pick_port(args.port)
    private_dir(output)
    ccs_home = output / "ccs-home"
    codex_home = output / "codex-home"
    private_dir(ccs_home)
    private_dir(ccs_home / ".cc-switch")
    private_dir(codex_home)
    other_homes = {}
    for app in ("claude", "gemini", "grokbuild", "opencode", "openclaw", "hermes", "pi"):
        directory = output / (app + "-home")
        private_dir(directory)
        other_homes[app] = directory

    database_path = ccs_home / ".cc-switch" / "cc-switch.db"
    backup_database(source_db, database_path)

    connection = sqlite3.connect(str(database_path))
    try:
        if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            fail("isolated database is not a compatible CC Switch v4 schema (user_version 20) database")
        columns = [row[1] for row in connection.execute("PRAGMA table_info(providers)")]
        if not PROVIDER_COLUMNS.issubset(set(columns)):
            fail("isolated provider table lacks compatible CC Switch v4 schema columns")
        proxy_columns = [row[1] for row in connection.execute("PRAGMA table_info(proxy_config)")]
        for needed in ("app_type", "proxy_enabled", "enabled", "auto_failover_enabled",
                       "listen_address", "listen_port", "live_takeover_active"):
            if needed not in proxy_columns:
                fail("isolated proxy_config table lacks compatible CC Switch v4 schema columns")

        rows = list(connection.execute(
            "SELECT id, settings_config, meta FROM providers WHERE app_type='codex' AND is_current=1"))
        if not rows:
            fail("source database has no current Codex provider row")
        if len(rows) != 1:
            fail("source database has more than one current Codex provider row")
        current_id, current_settings_text, current_meta_text = rows[0]
        try:
            current_settings = json.loads(current_settings_text)
            current_meta = json.loads(current_meta_text) if current_meta_text else {}
        except ValueError:
            fail("current Codex provider row is not valid JSON")
        if not isinstance(current_settings, dict) or not isinstance(current_meta, dict):
            fail("current Codex provider row has an invalid shape")
        if current_meta.get("apiFormat") not in (None, "openai_responses"):
            fail("current Codex provider is not a native Responses provider")
        if connection.execute(
                "SELECT COUNT(*) FROM providers WHERE app_type='codex' AND id=?",
                (current_id,)).fetchone()[0] != 1:
            fail("current Codex provider row is ambiguous")

        card_text = current_settings.get("config")
        if not isinstance(card_text, str):
            fail("current Codex provider card has no config text")
        card_base = extract_base_url(card_text)
        card_model = extract_model(card_text)
        if not card_model:
            fail("current Codex provider card has no top-level model")
        rewritten = rewrite_card_config(card_text, card_base, live_base_url, api_key,
                                        current_settings.get("auth"), live_config)
        connection.execute(
            "UPDATE providers SET settings_config=?, in_failover_queue=0 WHERE id=? AND app_type='codex'",
            (json.dumps(rewritten, ensure_ascii=False, separators=(",", ":")), current_id))

        now_ms = int(time.time() * 1000)
        workbuddy_id = "workbuddy-deepseek-" + str(now_ms)
        workbuddy_settings = {
            "base_url": WORKBUDDY_BASE_URL.rstrip("/") + "/v1",
            "auth": {"OPENAI_API_KEY": workbuddy_api_key},
            "config": tool_profile_text(WORKBUDDY_BASE_URL, WORKBUDDY_MODEL, WORKBUDDY_WINDOW,
                                       "WorkBuddy DeepSeek"),
            "modelCatalog": {"models": [{
                "model": WORKBUDDY_MODEL,
                "displayName": "DeepSeek V4.1 Flash (WorkBuddy CN)",
                "contextWindow": WORKBUDDY_WINDOW,
                "supportsParallelToolCalls": True,
                "inputModalities": ["text", "image"],
                "reasoningLevels": ["low", "high", "max"],
                "defaultReasoningLevel": "high",
            }]},
        }
        workbuddy_meta = {"apiFormat": "openai_chat", "endpointAutoSelect": False}
        connection.execute(
            "INSERT INTO providers (id, app_type, name, settings_config, website_url, category, "
            "created_at, icon, meta, is_current, in_failover_queue, cost_multiplier) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (workbuddy_id, "codex", "WorkBuddy DeepSeek",
             json.dumps(workbuddy_settings, ensure_ascii=False, separators=(",", ":")),
             WORKBUDDY_BASE_URL, "custom", now_ms, "workbuddy",
             json.dumps(workbuddy_meta, ensure_ascii=False, separators=(",", ":")), 0, 0, "1.0"))
        connection.execute("UPDATE providers SET in_failover_queue=0 WHERE app_type='codex'")
        connection.execute(
            "UPDATE proxy_config SET enabled=1, auto_failover_enabled=0, live_takeover_active=0 "
            "WHERE app_type='codex'")
        connection.execute(
            "UPDATE proxy_config SET proxy_enabled=1, listen_address='127.0.0.1', listen_port=? "
            "WHERE app_type='claude'",
            (port,))
        connection.commit()
    finally:
        connection.close()

    apps = live_state.get("apps") if isinstance(live_state, dict) else None
    if not isinstance(apps, dict):
        fail("source live-state.json has no apps mapping")
    codex_state = apps.get("codex") if isinstance(apps.get("codex"), dict) else {}
    old_stack = codex_state.get("stack") if isinstance(codex_state.get("stack"), dict) else {}
    registry = dict(old_stack.get("keys", {}))
    if any(not isinstance(key, str) or not isinstance(owner, str) for key, owner in registry.items()):
        fail("source Codex stack key registry is invalid")

    default_key = allocate_key(registry, current_id, str(current_meta.get("icon") or provider_name))
    registry[default_key] = current_id
    workbuddy_key = allocate_key(registry, workbuddy_id, "workbuddy")
    registry[workbuddy_key] = workbuddy_id

    members = [current_id, workbuddy_id]

    new_state = {
        "version": 1,
        "apps": {
            "codex": {
                "mode": "proxy",
                "attached": True,
                "proxy_route": current_id,
                "stack": {"enabled": True, "members": members, "keys": registry},
            },
            "claude": {"mode": "direct"},
            "gemini": {"mode": "direct"},
            "grokbuild": {"mode": "direct"},
        },
    }

    output_settings = dict(live_settings)
    output_settings["enableStackMode"] = True
    output_settings["codexStackClassicSubagents"] = False
    output_settings["currentProviderCodex"] = current_id
    output_settings["enableLocalProxy"] = True
    output_settings["launchOnStartup"] = False
    output_settings["silentStartup"] = True
    output_settings["preserveCodexOfficialAuthOnSwitch"] = False
    output_settings["codexConfigDir"] = str(codex_home)
    for app, directory in other_homes.items():
        output_settings[app + "ConfigDir"] = str(directory)

    native_profile = {"version": 1, "base_url": live_base_url, "api_key": api_key,
                      "provider_name": provider_name}
    routes_config = {
        "version": 1,
        "upstream": "http://127.0.0.1:" + str(port) + "/v1",
        "model_routes": {WORKBUDDY_ALIAS: "ccs-" + workbuddy_key + "/" + WORKBUDDY_MODEL},
        "native_profile": "native-upstream.local.json",
    }

    write_private(codex_home / "config.toml",
                  isolated_client_text(live_config, client_id, client_table, api_key))
    write_private(ccs_home / ".cc-switch" / "settings.json",
                  json.dumps(output_settings, ensure_ascii=False, indent=2) + "\n")
    write_private(ccs_home / ".cc-switch" / "live-state.json",
                  json.dumps(new_state, ensure_ascii=False, indent=2) + "\n")
    write_private(output / "native-upstream.local.json",
                  json.dumps(native_profile, ensure_ascii=False, indent=2) + "\n")
    write_private(output / "routes-config.local.json",
                  json.dumps(routes_config, ensure_ascii=False, indent=2) + "\n")

    manifest = {
        "version": 1,
        "createdAtMs": now_ms,
        "port": port,
        "providerIds": {"default": current_id, "workbuddy": workbuddy_id},
        "stackKeys": {"default": default_key, "workbuddy": workbuddy_key},
        "paths": {
            "output": str(output),
            "ccsHome": str(ccs_home),
            "ccsConfigDir": str(ccs_home / ".cc-switch"),
            "codexConfigDir": str(codex_home),
            "database": str(database_path),
            "nativeProfile": str(output / "native-upstream.local.json"),
            "routesConfig": str(output / "routes-config.local.json"),
        },
        "launchEnv": {"CC_SWITCH_TEST_HOME": str(ccs_home)},
        "sourceBaselineSha256": baseline,
        "credentialSource": credential_source,
        "isolatedModel": card_model,
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    if any(secret in manifest_text for secret in (api_key, workbuddy_api_key)):
        fail("manifest would contain a credential")
    write_private(output / "manifest.json", manifest_text)

    for path in (codex_home / "config.toml", database_path,
                 ccs_home / ".cc-switch" / "settings.json",
                 ccs_home / ".cc-switch" / "live-state.json",
                 output / "native-upstream.local.json", output / "routes-config.local.json",
                 output / "manifest.json"):
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            fail("prepared file is not private: " + path.name)
    if stat.S_IMODE(output.stat().st_mode) & 0o077:
        fail("prepared output directory is not private")

    print(json.dumps({
        "status": "PREPARED",
        "message": "Isolated CC Switch home prepared; nothing was started and no source file was modified.",
        "outputDir": str(output),
        "port": port,
        "defaultProvider": current_id,
        "workbuddyProvider": workbuddy_id,
        "stackKeys": {"default": default_key, "workbuddy": workbuddy_key},
        "workbuddyModelRef": routes_config["model_routes"][WORKBUDDY_ALIAS],
        "credentialSource": credential_source,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PrepareError as error:
        print(json.dumps({"status": "ERROR", "error": str(error)}, ensure_ascii=False))
        sys.exit(1)
