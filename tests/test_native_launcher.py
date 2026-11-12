"""Account-scoped native launch lifecycle and legacy migration, with disposable data."""
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
from claude_native import NativeLauncher, native_args, publish_history
from claude_sessions import Sessions
from test_accounts import MemoryVault
from test_sessions import FakeClaude, profile


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

    def test_migration_keeps_original_and_alias_uses_native_name(self):
        legacy = Sessions(self.home, self.manager.root, self.vault, "fake", self.base)
        legacy.run("billing", "work", self.project)
        original = Path(legacy.record("billing")["transcript"])
        raw = original.read_bytes()
        self.manager.run("personal", self.project, ["--resume", "billing"])
        args = self.calls[-1][0]
        resumed = Path(args[args.index("--resume") + 1])
        self.assertTrue(resumed.is_relative_to(self.manager.history))
        self.assertEqual(resumed.read_bytes(), raw)
        self.assertEqual(original.read_bytes(), raw)
        self.assertIn("--name", args)
        self.assertEqual(legacy.record("billing")["legacy_profile"], "work")
        self.assertFalse((original.parents[2] / ".credentials.json").exists())
        self.assertEqual(self.manager.dependencies("work"), [])

    def test_migration_conflict_preserves_original_credentials_and_record(self):
        legacy = Sessions(self.home, self.manager.root, self.vault, "fake", self.base)
        legacy.run("billing", "work", self.project)
        record = legacy.record("billing")
        original = Path(record["transcript"])
        dest = self.manager.history / original.parent.name / original.name
        private_directory(dest.parent)
        dest.write_text("different native history", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "conflict"):
            self.manager.run("personal", self.project)
        self.assertEqual(legacy.record("billing"), record)
        self.assertTrue((legacy.folder("billing") / "config" / ".credentials.json").exists())
        self.assertEqual(dest.read_text(), "different native history")

    def test_atomic_migration_does_not_replace_a_racing_writer(self):
        source = Path(self.tmp.name) / "source.jsonl"
        destination = Path(self.tmp.name) / "destination.jsonl"
        source.write_text("old history", encoding="utf-8")
        original_rename = Path.rename
        def race(staged, target):
            Path(target).write_text("racing history", encoding="utf-8")
            return original_rename(staged, target)
        with patch.object(Path, "rename", race):
            with self.assertRaisesRegex(RuntimeError, "Another writer"):
                publish_history(source, destination)
        self.assertEqual(source.read_text(), "old history")
        self.assertEqual(destination.read_text(), "racing history")

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

    def test_migrated_alias_stops_shadowing_native_names_and_compatibility_resume_survives(self):
        legacy = Sessions(self.home, self.manager.root, self.vault, "fake", self.base)
        legacy.run("billing", "work", self.project)
        self.manager.run("personal", self.project, ["--resume", "billing"])
        self.assertTrue(legacy.record("billing")["native_named"])
        self.manager.run("work", self.project, ["--resume", "billing"])
        args = self.calls[-1][0]
        self.assertEqual(args[-2:], ["--resume", "billing"])
        self.manager.resume_alias("billing", account="personal")
        args = self.calls[-1][0]
        self.assertEqual(args[-1], legacy.record("billing")["native_transcript"])
        self.assertNotIn("--name", args)

    def test_migration_recovers_newer_legacy_refresh_but_preserves_renewed_generation(self):
        for name, renewed in (("billing", False), ("audit", True)):
            legacy = Sessions(self.home, self.manager.root, self.vault, "fake", self.base)
            legacy.run(name, "work", self.project)
            fresh = profile("work", expiry=6102444800000)
            private_json(legacy.folder(name) / "config" / ".credentials.json", {"claudeAiOauth": fresh["oauth"]})
            if renewed:
                original = profile("work")
                original["generation"] = "new-login"
                self.vault.put("profile/work", original)
            self.manager.migrate_legacy()
            if renewed:
                self.assertEqual(self.vault.get("profile/work"), original)
            else:
                self.assertEqual(self.vault.get("profile/work")["oauth"], fresh["oauth"])

    def test_external_legacy_import_migrates_its_auxiliary_bundle(self):
        legacy = Sessions(self.home, self.manager.root, self.vault, "fake", self.base)
        legacy.run("billing", "work", self.project)
        record = legacy.record("billing")
        external = Path(self.tmp.name) / "external"
        private_directory(external)
        source = external / (record["session_id"] + ".jsonl")
        source.write_bytes(Path(record["transcript"]).read_bytes())
        bundle = external / record["session_id"] / "tool-results"
        private_directory(bundle)
        (bundle / "result.txt").write_text("retained tool output", encoding="utf-8")
        record.update(transcript=str(source), last_config=str(external))
        legacy.write(record)
        self.manager.migrate_legacy()
        target = Path(legacy.record("billing")["native_transcript"])
        self.assertEqual((target.parent / record["session_id"] / "tool-results" / "result.txt").read_text(), "retained tool output")
        self.assertTrue(source.exists())

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

    def test_migration_recovers_a_shared_legacy_chats_retained_login(self):
        legacy = Sessions(self.home, self.manager.root, self.vault, "fake", self.base)
        legacy.run("billing", "work", self.project)
        record = legacy.record("billing")
        record.update(profile=None, retained_profile="work", mode="shared", epoch=None)
        legacy.write(record)
        fresh = profile("work", expiry=6102444800000)
        private_json(legacy.folder("billing") / "config" / ".credentials.json", {"claudeAiOauth": fresh["oauth"]})
        self.manager.migrate_legacy()
        self.assertEqual(self.vault.get("profile/work")["oauth"], fresh["oauth"])
        self.assertIsNone(legacy.record("billing")["retained_profile"])
