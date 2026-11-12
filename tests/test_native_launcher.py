"""Account-scoped native launch lifecycle with disposable data."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid

from claude_account import obj, private_json
from claude_account_windows import LeaseBusyError, SessionLease, create_history_junction, private_directory
from claude_native import NativeLauncher, native_args
from test_accounts import MemoryVault
from support import FakeClaude, profile


class NativeLaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir()
        private_directory(self.home / ".claude")
        self.vault = MemoryVault()
        for name in ("work", "personal"):
            self.vault.put("profile/" + name, profile(name))
        self.default("personal")
        self.calls, self.during = [], None
        self.fail = False
        self.base = FakeClaude(self.home)
        self.manager = NativeLauncher(self.home, Path(self.tmp.name) / "managed", self.vault, "fake", self.invoke)

    def default(self, name):
        private_json(self.home / ".claude.json", {"oauthAccount": profile(name)["account"]})
        private_json(self.home / ".claude" / ".credentials.json", {"claudeAiOauth": profile(name)["oauth"]})

    def invoke(self, args, *, env, cwd, capture=False, lease=None):
        if capture:
            return self.base(args, env=env, cwd=cwd, capture=True)
        if self.fail:
            raise OSError("invented launch failure")
        config = Path(env["CLAUDE_CONFIG_DIR"])
        self.calls.append((args, env, config))
        (config / "projects" / "synthetic-history.txt").write_text("history", encoding="utf-8")
        if self.during:
            self.during(args, env, config)
        return subprocess.CompletedProcess(args, 0)

    def test_native_options_forwarded_no_historical_registry_and_credentials_scrubbed(self):
        before = (self.home / ".claude" / ".credentials.json").read_bytes()
        self.manager.run("work", self.project, ["--name", "Billing report", "--model", "sonnet"])
        args, env, config = self.calls[-1]
        self.assertEqual(args[-4:], ["--name", "Billing report", "--model", "sonnet"])
        self.assertEqual(env["CLAUDE_ACCOUNT_PROFILE"], "work")
        self.assertFalse(config.exists())
        self.assertEqual(list(self.manager.launches.iterdir()), [])
        self.assertFalse((self.manager.root / "sessions").exists())
        self.assertEqual((self.home / ".claude" / ".credentials.json").read_bytes(), before)
        self.assertEqual((self.manager.history / "synthetic-history.txt").read_text(), "history")

    def test_picker_continue_and_resume_are_native_arguments(self):
        for args in (["--resume"], ["--continue"], ["--resume", "native-title"],
                     ["--resume", str(uuid.uuid4()), "--fork-session"]):
            self.manager.run("personal", self.project, args)
            self.assertEqual(self.calls[-1][0][-len(args):], args)

    def test_default_changes_and_reaping_do_not_affect_running_launch(self):
        def during(args, env, config):
            before = (config / ".credentials.json").read_bytes()
            self.default("work")
            self.manager.reap()
            self.assertEqual((config / ".credentials.json").read_bytes(), before)
            self.assertEqual(self.manager.active()[0]["profile"], "personal")
        self.during = during
        self.manager.run("personal", self.project)

    def test_failed_launch_leaves_no_credentials_or_history_loss(self):
        self.fail = True
        with self.assertRaises(OSError):
            self.manager.run("work", self.project)
        self.assertEqual(list(self.manager.launches.iterdir()), [])
        self.assertTrue(self.manager.history.is_dir())

    def test_cleanup_still_scrubs_credentials_when_history_link_is_replaced(self):
        def damage(args, env, config):
            (config / "projects").rmdir()
            other = Path(self.tmp.name) / "other"
            private_directory(other)
            (other / "keep.txt").write_text("keep", encoding="utf-8")
            create_history_junction(config / "projects", other)
        self.during = damage
        with self.assertRaisesRegex(RuntimeError, "cleanup"):
            self.manager.run("work", self.project)
        config = self.calls[-1][2]
        self.assertFalse((config / ".credentials.json").exists())
        self.assertFalse((config / ".claude.json").exists())
        self.assertTrue((Path(self.tmp.name) / "other" / "keep.txt").exists())
        (config / "projects").rmdir()

    def test_native_refresh_does_not_overwrite_changed_saved_account(self):
        def concurrent(args, env, config):
            fresh = profile("work")
            fresh["oauth"]["accessToken"] = "invented-refreshed"
            private_json(config / ".credentials.json", {"claudeAiOauth": fresh["oauth"]})
            self.vault.put("profile/work", profile("work", expiry=5102444800000))
        self.during = concurrent
        self.manager.run("work", self.project)
        self.assertEqual(self.vault.get("profile/work")["oauth"]["expiresAt"], 5102444800000)

    def test_manual_login_is_not_saved_under_original_label(self):
        def login(args, env, config):
            private_json(config / ".claude.json", {"oauthAccount": profile("personal")["account"]})
            private_json(config / ".credentials.json", {"claudeAiOauth": profile("personal")["oauth"]})
        self.during = login
        self.manager.run("work", self.project)
        self.assertEqual(self.vault.get("profile/work"), profile("work"))


    def test_auth_and_background_escape_options_are_rejected(self):
        for args in (["--bg"], ["--settings", "unsafe.json"], ["--bare"], ["auth"],
                     ["--permission-mode", "bypassPermissions"]):
            with self.assertRaises(RuntimeError):
                native_args(args)
        self.assertEqual(native_args(["--resume=billing"]), ["--resume", "billing"])

    def test_disabled_hooks_are_supported_without_custom_lifecycle_tracking(self):
        private_json(self.home / ".claude" / "settings.json", {"disableAllHooks": True})
        self.manager.run("work", self.project)

    def test_malformed_crash_metadata_does_not_leave_credentials(self):
        self.manager._initialize()
        folder = self.manager.launches / uuid.uuid4().hex
        self.manager.seed(folder / "config", profile("work"))
        create_history_junction(folder / "config" / "projects", self.manager.history)
        (folder / "native.json").write_text("broken metadata", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "damaged metadata"):
            self.manager.reap()
        self.assertFalse((folder / "config" / ".credentials.json").exists())
        self.assertTrue(self.manager.history.is_dir())
        self.manager.reap()
        self.assertFalse(folder.exists())


    def test_registry_contention_after_exit_defers_only_empty_metadata(self):
        held = []
        def during(args, env, config):
            held.append(SessionLease(self.manager.root / ".registry").__enter__())
        self.during = during
        try:
            self.assertEqual(self.manager.run("work", self.project), 0)
            self.assertFalse(self.calls[-1][2].exists())
        finally:
            for lease in held:
                lease.__exit__()
        self.manager.reap()
        self.assertEqual(list(self.manager.launches.iterdir()), [])

    def test_active_listing_holds_registry_while_probing_launches(self):
        self.manager._initialize()
        original = self.manager._active_locked
        def probe(display):
            with self.assertRaises(LeaseBusyError):
                with SessionLease(self.manager.root / ".registry"):
                    self.fail("Listing released registry before its lease probes")
            return original(display)
        with patch.object(self.manager, "_active_locked", probe):
            self.assertEqual(self.manager.active(), [])
