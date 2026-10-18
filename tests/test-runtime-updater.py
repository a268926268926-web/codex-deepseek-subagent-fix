#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


launcher = load("runtime_cache_test_launcher", "launch-routed-codex.py")
updater = load("runtime_cache_test_updater", "update-runtime.py")


class RuntimeUpdateTests(unittest.TestCase):
    def test_complete_check_records_private_cache_and_matches_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "runtime-compatibility.local.json"
            fingerprint = {"codex": "validated-runtime"}
            checks = dict.fromkeys(launcher.REQUIRED_RUNTIME_CHECKS, True)
            updater.record_verified_runtime(path, "0.164.0-test", checks, fingerprint)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with mock.patch.object(launcher, "__file__", str(directory / "launch-routed-codex.py")), \
                    mock.patch.object(launcher, "runtime_fingerprint", return_value=fingerprint):
                self.assertTrue(launcher.cached_runtime_verified(Path("/unused/codex"),
                                                                 "0.164.0-test", Path("/unused/models")))
                self.assertFalse(launcher.cached_runtime_verified(Path("/unused/codex"),
                                                                  "0.165.0-test", Path("/unused/models")))

    def test_failed_check_preserves_previously_verified_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime-compatibility.local.json"
            path.write_text("previous valid result")
            checks = dict.fromkeys(launcher.REQUIRED_RUNTIME_CHECKS, True)
            checks["v2_enabled"] = False
            with self.assertRaises(RuntimeError):
                updater.record_verified_runtime(path, "0.164.0-test", checks, {})
            self.assertEqual(path.read_text(), "previous valid result")

    def test_changed_inputs_invalidate_cached_compatibility(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "runtime-compatibility.local.json"
            updater.record_verified_runtime(path, "0.164.0-test",
                                            dict.fromkeys(launcher.REQUIRED_RUNTIME_CHECKS, True),
                                            {"codex": "before-update"})
            with mock.patch.object(launcher, "__file__", str(directory / "launch-routed-codex.py")), \
                    mock.patch.object(launcher, "runtime_fingerprint", return_value={"codex": "after-update"}):
                self.assertFalse(launcher.cached_runtime_verified(Path("/unused/codex"),
                                                                  "0.164.0-test", Path("/unused/models")))

    def test_incomplete_or_malformed_cache_cannot_enable_a_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "runtime-compatibility.local.json"
            with mock.patch.object(launcher, "__file__", str(directory / "launch-routed-codex.py")):
                for text in ("[]", "invalid-json", json.dumps({"schema": 1, "runtime": "0.164.0-test",
                                                              "checks": {}, "sha256": {}})):
                    path.write_text(text)
                    self.assertFalse(launcher.cached_runtime_verified(Path("/unused/codex"),
                                                                      "0.164.0-test", Path("/unused/models")))


if __name__ == "__main__":
    unittest.main()
