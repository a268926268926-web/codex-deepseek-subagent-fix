#!/usr/bin/env python3
"""Minimal black-box fixture tests for prepare-workbuddy-ccs.py.

Uses only synthetic fixtures in a temporary directory: no real CC Switch home,
no real Codex home, no network, no service start, and no real credential.
"""
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
HELPER = SCRIPTS / "prepare-workbuddy-ccs.py"

CARD_ID = "fixture-card-1"
OLD_CARD_URL = "https://cdn.fixture.example/v1"
LIVE_URL = "https://source.fixture.example/v1"
BEARER = "fixture-bearer-key-1"
AUTH_JSON_KEY = "fixture-auth-json-key-2"
WORKBUDDY_KEY = "fixture-workbuddy-key-3"
ENV_KEY = "fixture-env-key-4"

PROVIDER_COLUMNS = ("id", "app_type", "name", "settings_config", "website_url", "category",
                    "created_at", "sort_index", "notes", "icon", "icon_color", "meta",
                    "is_current", "in_failover_queue", "cost_multiplier", "limit_daily_usd",
                    "limit_monthly_usd", "provider_type")
PROXY_COLUMNS = ("app_type", "proxy_enabled", "enabled", "auto_failover_enabled",
                 "listen_address", "listen_port", "live_takeover_active")


def write(path, text, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.chmod(path, mode)


def card_text(url=OLD_CARD_URL):
    return ("model_provider = \"custom\"\n"
            "model = \"gpt-6.1-sol\"\n"
            "model_reasoning_effort = \"high\"\n"
            "\n[model_providers.custom]\n"
            "name = \"Fixture Card\"\n"
            f'"base_url" = "{url}"\n'
            "wire_api = \"responses\"\n"
            "requires_openai_auth = false\n")


def digest_tree(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Fixture:
    def __init__(self, root, codex_registry=None, claude_registry=None, use_env_key=False):
        self.root = root
        self.ccs = root / "ccs"
        self.codex = root / "codex"
        self.workbuddy = root / "workbuddy-config.json"
        self.output = root / "out"
        self.us_env_key = use_env_key
        self._build(codex_registry or {}, claude_registry or {})

    def _build(self, codex_registry, claude_registry):
        self.ccs.mkdir(parents=True, exist_ok=True)
        self.codex.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.ccs / "cc-switch.db")
        try:
            connection.execute("PRAGMA user_version = 20")
            connection.execute("CREATE TABLE providers (" + ", ".join(
                column + " TEXT" for column in PROVIDER_COLUMNS) + ")")
            connection.execute("CREATE TABLE proxy_config (" + ", ".join(
                column + " TEXT" for column in PROXY_COLUMNS) + ")")
            connection.execute(
                "INSERT INTO providers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (CARD_ID, "codex", "Fixture Card",
                 json.dumps({"auth": {"OPENAI_API_KEY": AUTH_JSON_KEY}, "config": card_text()}),
                 "https://cdn.fixture.example", "custom", "0", None, None, None, None,
                 json.dumps({"apiFormat": "openai_responses", "commonConfigEnabled": True}),
                 "1", "1", "1.0", None, None, None))
            connection.execute("INSERT INTO providers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               ("fixture-claude", "claude", "Claude", "{}", None, "custom",
                                "0", None, None, None, None, "{}", "1", "0", "1.0", None, None, None))
            for app in ("claude", "codex", "gemini", "grokbuild"):
                connection.execute("INSERT INTO proxy_config VALUES (?,?,?,?,?,?,?)",
                                   (app, "0", "0", "1", "127.0.0.1", "15721", "0"))
            connection.commit()
        finally:
            connection.close()
        os.chmod(self.ccs / "cc-switch.db", 0o600)

        states = {"version": 1, "apps": {
            "claude": {"mode": "direct", "stack": {"enabled": True, "members": [],
                                                       "keys": dict(claude_registry)}},
            "codex": {"mode": "direct", "proxy_route": "legacy-other-provider",
                      "stack": {"enabled": False, "members": [], "keys": dict(codex_registry)}},
            "gemini": {"mode": "direct"}}}
        write(self.ccs / "live-state.json", json.dumps(states))
        write(self.ccs / "settings.json", json.dumps({"enableStackMode": False, "language": "zh"}))

        provider = "model_provider = \"custom\"\nmodel = \"gpt-6.1-sol\"\nmodel_reasoning_effort = \"low\"\n"
        provider += "model_context_window = 300000\n\n[model_providers.custom]\n"
        provider += "name = \"Fixture Relay\"\n" + f"base_url = \"{LIVE_URL}\"\n"
        provider += "wire_api = \"responses\"\nrequires_openai_auth = false\n"
        if not self.us_env_key:
            provider += f"experimental_bearer_token = \"{BEARER}\"\n"
        else:
            provider += "env_key = \"FIXTURE_ORIGINAL_KEY\"\n"
        write(self.codex / "config.toml", provider)
        write(self.codex / "auth.json", json.dumps({"OPENAI_API_KEY": AUTH_JSON_KEY}))
        write(self.workbuddy, json.dumps({"api_key": WORKBUDDY_KEY, "listen": "127.0.0.1:7863"}))

    def env(self):
        environment = dict(os.environ)
        if self.us_env_key:
            environment["FIXTURE_ORIGINAL_KEY"] = ENV_KEY
        return environment

    def run(self):
        command = [sys.executable, str(HELPER),
                   "--source-ccs-dir", str(self.ccs),
                   "--source-codex-dir", str(self.codex),
                   "--workbuddy-config", str(self.workbuddy),
                   "--output-dir", str(self.output),
                   "--port", str(free_port())]
        return subprocess.run(command, capture_output=True, text=True, timeout=60,
                              env=self.env())

    def database(self):
        return sqlite3.connect(str(self.output / "ccs-home" / ".cc-switch" / "cc-switch.db"))


class PrepareWorkBuddyTests(unittest.TestCase):
    def prepared(self, fixture):
        result = fixture.run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_one_full_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary))
            before = {**digest_tree(fixture.ccs), **digest_tree(fixture.codex)}
            result = fixture.run()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            manifest = json.loads(result.stdout)
            for secret in (BEARER, WORKBUDDY_KEY, ENV_KEY, AUTH_JSON_KEY):
                self.assertNotIn(secret, result.stdout + result.stderr)
            self.assertEqual({**digest_tree(fixture.ccs), **digest_tree(fixture.codex)}, before)
            for path in fixture.output.rglob("*"):
                mode = stat.S_IMODE(path.stat().st_mode)
                self.assertEqual(mode, 0o700 if path.is_dir() else 0o600, str(path))

            state = json.loads((fixture.output / "ccs-home" / ".cc-switch" /
                                "live-state.json").read_text())
            codex = state["apps"]["codex"]
            self.assertEqual(codex["proxy_route"], CARD_ID)
            self.assertEqual(codex["stack"]["keys"][manifest["stackKeys"]["default"]], CARD_ID)
            routes = json.loads((fixture.output / "routes-config.local.json").read_text())
            target = routes["model_routes"]["cn:deepseek-v4.1-flash"]
            self.assertNotIn("gpt-5.6-luna", routes["model_routes"])
            self.assertTrue(target.startswith("ccs-" + manifest["stackKeys"]["workbuddy"] + "/"))
            self.assertTrue(target.endswith("/cn:deepseek-v4.1-flash"))

            native = json.loads((fixture.output / "native-upstream.local.json").read_text())
            live = (fixture.output / "codex-home" / "config.toml").read_text()
            self.assertEqual(native["api_key"], BEARER)
            self.assertIn(BEARER, live)
            self.assertNotIn(WORKBUDDY_KEY, native["api_key"])
            manifest_text = (fixture.output / "manifest.json").read_text()
            for secret in (BEARER, WORKBUDDY_KEY, AUTH_JSON_KEY, ENV_KEY):
                self.assertNotIn(secret, manifest_text)

            connection = fixture.database()
            try:
                card_settings = json.loads(connection.execute(
                    "SELECT settings_config FROM providers WHERE app_type='codex' AND id=?",
                    (CARD_ID,)).fetchone()[0])
                card = card_settings["config"]
                workbuddy = json.loads(connection.execute(
                    "SELECT settings_config FROM providers WHERE app_type='codex' AND id=?",
                    (manifest["workbuddyProvider"],)).fetchone()[0])
                queue = connection.execute(
                    "SELECT COUNT(*) FROM providers WHERE app_type='codex' AND in_failover_queue!=0"
                ).fetchone()[0]
                proxy = dict((row[0], row[1:]) for row in connection.execute(
                    "SELECT app_type, proxy_enabled, enabled, auto_failover_enabled FROM proxy_config"))
            finally:
                connection.close()
            self.assertIn(LIVE_URL, card)
            self.assertNotIn(OLD_CARD_URL, card)
            self.assertEqual(card_settings["base_url"], LIVE_URL)
            self.assertIn('model_reasoning_effort = "low"', card)
            self.assertNotIn('model_reasoning_effort = "high"', card)
            model = workbuddy["modelCatalog"]["models"][0]
            self.assertEqual(model["reasoningLevels"], ["low", "high", "max"])
            self.assertEqual(model["defaultReasoningLevel"], "high")
            self.assertEqual(model["inputModalities"], ["text", "image"])
            self.assertEqual(queue, 0)
            self.assertEqual(proxy["claude"][0], "1")
            self.assertEqual(proxy["codex"][1], "1")
            self.assertEqual(proxy["codex"][2], "0")

            manifest_hash = digest_tree(fixture.output)["manifest.json"]
            again = fixture.run()
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(digest_tree(fixture.output)["manifest.json"], manifest_hash)

    def test_two_registry_is_per_app_and_immutable(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary),
                              codex_registry={"workbuddy": "deleted-provider",
                                              "old-default": CARD_ID, "retired": "deleted-2"},
                              claude_registry={"claude-only": "fixture-claude"})
            manifest = self.prepared(fixture)
            state = json.loads((fixture.output / "ccs-home" / ".cc-switch" /
                                "live-state.json").read_text())
            keys = state["apps"]["codex"]["stack"]["keys"]
            self.assertEqual(manifest["stackKeys"]["workbuddy"], "workbuddy-2")
            self.assertEqual(keys["workbuddy"], "deleted-provider")
            self.assertEqual(keys["workbuddy-2"], manifest["workbuddyProvider"])
            self.assertEqual(manifest["stackKeys"]["default"], "old-default")
            self.assertEqual(keys["old-default"], CARD_ID)
            self.assertNotIn("claude-only", keys)

    def test_three_env_key_precedes_auth_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Fixture(Path(temporary), use_env_key=True)
            manifest = self.prepared(fixture)
            native = json.loads((fixture.output / "native-upstream.local.json").read_text())
            self.assertEqual(native["api_key"], ENV_KEY)
            self.assertEqual(manifest["credentialSource"], "provider-environment")
            self.assertNotIn(ENV_KEY, json.dumps(manifest))


if __name__ == "__main__":
    unittest.main(verbosity=2)
