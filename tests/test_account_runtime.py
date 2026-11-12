"""Private login preparation and enrollment guards, with invented account data."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from claude_account import private_json
from claude_auth import AccountRuntime, AUTH_OVERRIDES, validate_environment
from test_accounts import MemoryVault
from support import FakeClaude, profile


class AccountRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        (self.home / ".claude").mkdir()
        self.vault = MemoryVault()
        self.vault.put("profile/work", profile("work"))
        self.fake = FakeClaude(self.home)
        self.runtime = AccountRuntime(self.home, Path(self.tmp.name) / "managed", self.vault, "fake", self.fake)
        private_json(self.home / ".claude.json", {"oauthAccount": profile("work")["account"]})
        private_json(self.home / ".claude" / ".credentials.json", {"claudeAiOauth": profile("work")["oauth"]})

    def test_auth_fixture_control_accepts_matching_account_and_rejects_logged_out(self):
        config = Path(self.tmp.name) / "private"
        self.runtime.seed(config, profile("work"))
        self.assertEqual(Path(self.runtime.verify(config, profile("work"), self.home)["CLAUDE_CONFIG_DIR"]), config)
        self.fake.status_failure = True
        with self.assertRaisesRegex(RuntimeError, "verify"):
            self.runtime.verify(config, profile("work"), self.home)

    def test_isolated_enrollment_leaves_shared_login_and_removes_temporary_credentials(self):
        before = (self.home / ".claude" / ".credentials.json").read_bytes()
        self.runtime.enroll("lab")
        self.assertEqual(self.vault.get("profile/lab")["account"], profile("lab")["account"])
        self.assertEqual((self.home / ".claude" / ".credentials.json").read_bytes(), before)
        self.assertEqual(list((self.runtime.root / "enrollment").iterdir()), [])

    def test_renewal_rejects_wrong_identity_and_duplicate_label(self):
        before = self.vault.get("profile/work")
        with self.assertRaisesRegex(RuntimeError, "different account"):
            self.runtime.enroll("work", renew=True)
        self.assertEqual(self.vault.get("profile/work"), before)
        self.fake.login_profile = profile("work")
        with self.assertRaisesRegex(RuntimeError, "already saved"):
            self.runtime.enroll("another")

    def test_enrollment_rechecks_identity_after_acquiring_lock(self):
        @contextmanager
        def concurrent(folder):
            self.vault.put("profile/work", profile("personal"))
            yield
        self.fake.login_profile = profile("work")
        with patch("claude_auth.operation_lock", concurrent):
            with self.assertRaisesRegex(RuntimeError, "different account"):
                self.runtime.enroll("work", renew=True)
        self.assertEqual(self.vault.get("profile/work")["account"], profile("personal")["account"])

    def test_renewal_token_and_generation_are_atomic_and_store_failure_cleans_up(self):
        self.fake.login_profile = profile("work", expiry=5102444800000)
        writes = []
        original = self.vault.put
        def only_profile(key, value):
            writes.append(key)
            self.assertTrue(value.get("generation"))
            original(key, value)
        with patch.object(self.vault, "put", only_profile):
            self.runtime.enroll("work", renew=True)
        self.assertEqual(writes, ["profile/work"])
        before = self.vault.get("profile/work")
        with patch.object(self.vault, "put", side_effect=OSError("invented store failure")):
            with self.assertRaises(OSError):
                self.runtime.enroll("work", renew=True)
        self.assertEqual(self.vault.get("profile/work"), before)
        self.assertEqual(list((self.runtime.root / "enrollment").iterdir()), [])

    def test_environment_and_settings_authentication_overrides_are_refused(self):
        validate_environment({})
        for name in AUTH_OVERRIDES:
            with self.assertRaises(RuntimeError):
                validate_environment({name.lower(): "invented-override"})
        settings = self.home / ".claude" / "settings.json"
        for value in ({"apiKeyHelper": "other"}, {"forceLoginMethod": "console"},
                      {"env": {"ANTHROPIC_API_KEY": "invented"}}):
            private_json(settings, value)
            with self.assertRaises(RuntimeError):
                self.runtime.check_settings(self.home)
        private_json(settings, {"disableAllHooks": True})
        self.runtime.check_settings(self.home)

    def test_auth_status_directory_and_identity_mismatch_fail_before_launch(self):
        config = Path(self.tmp.name) / "private"
        self.runtime.seed(config, profile("work"))
        for changes in ({"configDirectory": str(self.home / ".claude")},
                        {"email": "other@example.invalid"}, {"orgId": "other-org"}, {"authMethod": "api_key"}):
            base = self.fake(["fake", "auth", "status"], env=self.runtime.environment(config), cwd=self.home, capture=True)
            status = json.loads(base.stdout)
            status.update(changes)
            self.runtime.invoke = lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, json.dumps(status), "")
            with self.assertRaises(RuntimeError):
                self.runtime.verify(config, profile("work"), self.home)
