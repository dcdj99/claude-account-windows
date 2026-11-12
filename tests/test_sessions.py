"""Lifecycle tests: invented accounts, disposable files, no real credentials or network."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from contextlib import contextmanager

from claude_account import obj, private_json
from test_accounts import MemoryVault


def profile(name, expiry=4102444800000):
    return {"account": {"accountUuid": "id-" + name, "organizationUuid": "org-" + name,
                        "emailAddress": name + "@example.invalid"},
            "oauth": {"accessToken": "invented-" + name, "refreshToken": "invented-refresh-" + name,
                      "expiresAt": expiry, "scopes": ["user:profile", "user:inference"],
                      "subscriptionType": "max"}}


class FakeClaude:
    def __init__(self, home):
        self.home = home
        self.requests = []
        self.after_start = None
        self.status_failure = False
        self.launch_failure = False
        self.exit_code = 0
        self.login_profile = profile("lab")

    def __call__(self, args, *, env, cwd, capture=False, lease=None):
        config = Path(env.get("CLAUDE_CONFIG_DIR") or self.home / ".claude")
        settings = config / ".claude.json" if env.get("CLAUDE_CONFIG_DIR") else self.home / ".claude.json"
        if args[1:3] == ["auth", "login"]:
            private_json(settings, {"oauthAccount": self.login_profile["account"]})
            private_json(config / ".credentials.json", {"claudeAiOauth": self.login_profile["oauth"]})
            return subprocess.CompletedProcess(args, 0)
        data = obj(settings, True).get("oauthAccount", {})
        token = obj(config / ".credentials.json", True).get("claudeAiOauth", {}).get("accessToken")
        if args[1:3] == ["auth", "status"]:
            status = {"loggedIn": bool(token) and not self.status_failure, "authMethod": "claude.ai",
                      "configDirectory": str(config), "email": data.get("emailAddress"),
                      "orgId": data.get("organizationUuid")}
            return subprocess.CompletedProcess(args, 0, json.dumps(status), "")
        if self.launch_failure:
            raise OSError("invented launch failure")
        if "--resume" in args:
            src = Path(args[args.index("--resume") + 1])
            lines = src.read_text(encoding="utf-8").splitlines()
            sid = json.loads(lines[0])["sessionId"]
        else:
            sid = args[args.index("--session-id") + 1]
            lines = []
        transcript = config / "projects" / "test-project" / (sid + ".jsonl")
        transcript.parent.mkdir(parents=True, exist_ok=True)
        lines.append(json.dumps({"sessionId": sid, "type": "user", "message": {"content": "turn"}}))
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.requests.append({"token": token, "args": list(args), "env": dict(env), "history": len(lines),
                              "config": config, "settings": settings})
        if self.after_start:
            self.after_start(self.requests[-1])
        return subprocess.CompletedProcess(args, self.exit_code)


class AvailabilityTest(unittest.TestCase):
    def test_pinned_session_module_exists(self):
        self.assertIsNotNone(importlib.util.find_spec("claude_sessions"), "Pinned-session implementation is missing")


@unittest.skipUnless(importlib.util.find_spec("claude_sessions"), "implementation not added yet")
class SessionTests(unittest.TestCase):
    def setUp(self):
        from claude_sessions import Sessions
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir()
        self.vault = MemoryVault()
        for name in ("work", "personal", "lab"):
            self.vault.put("profile/" + name, profile(name))
        self.set_default("personal")
        self.fake = FakeClaude(self.home)
        self.manager = Sessions(self.home, Path(self.tmp.name) / "managed", self.vault, "fake-claude", invoke=self.fake)

    def set_default(self, name):
        p = profile(name)
        private_json(self.home / ".claude.json", {"oauthAccount": p["account"], "hasCompletedOnboarding": True})
        private_json(self.home / ".claude" / ".credentials.json", {"claudeAiOauth": p["oauth"]})

    def shared_bytes(self):
        return [(self.home / p).read_bytes() for p in (".claude.json", ".claude/.credentials.json")]

    def test_launch_saved_account_leaves_shared_login_unchanged(self):
        before = self.shared_bytes()
        self.manager.run("billing", "work", self.project)
        self.assertEqual(self.fake.requests[-1]["token"], "invented-work")
        self.assertEqual(self.shared_bytes(), before)
        self.assertEqual(self.manager.record("billing")["profile"], "work")

    def test_pin_survives_default_change_and_reopen(self):
        self.manager.run("billing", "work", self.project)
        self.set_default("lab")
        self.manager.resume("billing")
        self.assertEqual([r["token"] for r in self.fake.requests], ["invented-work", "invented-work"])
        self.assertEqual(self.fake.requests[-1]["history"], 2)

    def test_change_one_chat_preserves_history_and_other_chat_pin(self):
        self.manager.run("billing", "work", self.project)
        self.manager.run("docs", "work", self.project)
        self.manager.resume("billing", account="lab")
        self.manager.resume("billing")
        self.manager.resume("docs")
        self.assertEqual([r["token"] for r in self.fake.requests],
                         ["invented-work", "invented-work", "invented-lab", "invented-lab", "invented-work"])
        self.assertEqual(self.fake.requests[-2]["history"], 3)

    def test_shared_mode_follows_default_and_can_be_repinned(self):
        self.manager.run("billing", "work", self.project)
        self.manager.resume("billing", shared=True)
        self.set_default("lab")
        self.manager.resume("billing")
        self.manager.resume("billing", account="work")
        self.assertEqual([r["token"] for r in self.fake.requests],
                         ["invented-work", "invented-personal", "invented-lab", "invented-work"])
        self.assertEqual(self.fake.requests[-1]["history"], 4)

    def test_unknown_account_and_failed_preflight_do_not_change_existing_pin(self):
        self.manager.run("billing", "work", self.project)
        for account in ("missing", "lab"):
            self.fake.status_failure = account == "lab"
            with self.assertRaises(RuntimeError):
                self.manager.resume("billing", account=account)
            self.assertEqual(self.manager.record("billing")["profile"], "work")
        self.assertEqual(len(self.fake.requests), 1)

    def test_launch_failure_rolls_back_pin_and_does_not_replay_prompt(self):
        self.manager.run("billing", "work", self.project, args=["--model", "sonnet", "-p", "first task"])
        self.fake.launch_failure = True
        with self.assertRaises(OSError):
            self.manager.resume("billing", account="lab")
        self.assertEqual(self.manager.record("billing")["profile"], "work")
        self.fake.launch_failure = False
        self.manager.resume("billing")
        self.assertNotIn("first task", self.fake.requests[-1]["args"])
        self.assertNotIn("-p", self.fake.requests[-1]["args"])
        self.assertIn("sonnet", self.fake.requests[-1]["args"])

    def test_running_chat_rejects_second_launch_and_deletion(self):
        def while_running(request):
            with self.assertRaisesRegex(RuntimeError, "running|busy"):
                self.manager.resume("billing", account="lab")
            with self.assertRaisesRegex(RuntimeError, "running|busy"):
                self.manager.delete("billing", history=True, confirm=True)
        self.fake.after_start = while_running
        self.manager.run("billing", "work", self.project)

    def test_manual_login_mismatch_is_not_written_to_saved_profile(self):
        def change(request):
            private_json(request["settings"], {"oauthAccount": profile("lab")["account"]})
            private_json(request["config"] / ".credentials.json", {"claudeAiOauth": profile("lab")["oauth"]})
        self.fake.after_start = change
        self.manager.run("billing", "work", self.project)
        self.assertEqual(self.vault.get("profile/work")["account"], profile("work")["account"])
        self.fake.after_start = None
        with self.assertRaisesRegex(RuntimeError, "login|identity|account"):
            self.manager.resume("billing")
        self.manager.resume("billing", account="work")
        self.assertEqual(self.fake.requests[-1]["token"], "invented-work")

    def test_refreshed_credentials_saved_only_if_baseline_still_matches(self):
        refreshed = profile("work", expiry=4202444800000)
        refreshed["oauth"]["accessToken"] = "invented-refreshed"
        def refresh(request):
            private_json(request["config"] / ".credentials.json", {"claudeAiOauth": refreshed["oauth"]})
        self.fake.after_start = refresh
        self.manager.run("billing", "work", self.project)
        self.assertEqual(self.vault.get("profile/work")["oauth"], refreshed["oauth"])
        newer = profile("work", expiry=4302444800000)
        newer["oauth"]["accessToken"] = "invented-newer"
        def raced(request):
            self.vault.put("profile/work", newer)
            refresh(request)
        self.fake.after_start = raced
        self.manager.resume("billing")
        self.assertEqual(self.vault.get("profile/work")["oauth"], newer["oauth"])

    def test_isolated_enroll_and_renew_verify_identity_without_changing_default(self):
        before = self.shared_bytes()
        self.fake.login_profile = profile("client")
        self.manager.enroll("client")
        self.assertEqual(self.vault.get("profile/client")["oauth"], profile("client")["oauth"])
        self.fake.login_profile = profile("lab")
        with self.assertRaisesRegex(RuntimeError, "identity|account|different"):
            self.manager.enroll("work", renew=True)
        self.assertEqual(self.shared_bytes(), before)
        self.assertEqual(self.vault.get("profile/work"), profile("work"))

    def test_delete_requires_explicit_confirmation_and_preserves_history_by_default(self):
        self.manager.run("billing", "work", self.project)
        transcript = Path(self.manager.record("billing")["transcript"])
        with self.assertRaisesRegex(RuntimeError, "confirm|--yes"):
            self.manager.delete("billing")
        self.manager.delete("billing", confirm=True)
        self.assertTrue(transcript.exists())
        self.assertFalse((self.manager.root / "sessions" / "billing" / "config" / ".credentials.json").exists())

    def test_auth_overrides_and_session_escape_flags_are_rejected(self):
        from claude_sessions import validate_environment
        for key in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_SECURESTORAGE_CONFIG_DIR"):
            with self.assertRaises(RuntimeError):
                validate_environment({key: "invented-value"})
        for args in (["--resume", "another"], ["--bg"], ["--bare"], ["--settings", "other.json"]):
            with self.assertRaises(RuntimeError):
                self.manager.run("blocked", "work", self.project, args=args)
        self.assertEqual(self.fake.requests, [])

    def test_crash_record_discovers_saved_transcript_without_replaying_new_session(self):
        self.manager.run("billing", "work", self.project)
        record = self.manager.record("billing")
        record["transcript"] = None
        self.manager.write(record)
        self.manager.resume("billing")
        self.assertEqual(self.fake.requests[-1]["history"], 2)

    def test_manual_login_after_wrapper_crash_requires_explicit_account(self):
        self.manager.run("billing", "work", self.project)
        request = self.fake.requests[-1]
        private_json(request["settings"], {"oauthAccount": profile("lab")["account"]})
        private_json(request["config"] / ".credentials.json", {"claudeAiOauth": profile("lab")["oauth"]})
        with self.assertRaisesRegex(RuntimeError, "login|identity|account"):
            self.manager.resume("billing")

    def test_delete_also_removes_credential_backups(self):
        self.manager.run("billing", "work", self.project)
        config = self.fake.requests[-1]["config"]
        (config / ".credentials.json.bak").write_text("invented-backup", encoding="utf-8")
        self.manager.delete("billing", confirm=True)
        self.assertFalse((config / ".credentials.json.bak").exists())

    def test_renewal_takes_precedence_over_stale_longer_lived_session_token(self):
        self.manager.run("billing", "work", self.project)
        renewed = profile("work", expiry=4002444800000)
        renewed["oauth"]["accessToken"] = "invented-renewed"
        self.fake.login_profile = renewed
        self.manager.enroll("work", renew=True)
        self.manager.resume("billing")
        self.assertEqual(self.fake.requests[-1]["token"], "invented-renewed")

    def test_nonzero_child_exit_restores_previous_pin_and_keeps_new_history(self):
        self.manager.run("billing", "work", self.project)
        self.fake.exit_code = 1
        result = self.manager.resume("billing", account="lab")
        self.assertEqual(result, 1)
        self.assertEqual(self.manager.record("billing")["profile"], "work")
        self.fake.exit_code = 0
        self.manager.resume("billing")
        self.assertEqual(self.fake.requests[-1]["token"], "invented-work")
        self.assertEqual(self.fake.requests[-1]["history"], 3)

    def test_runtime_error_after_session_started_keeps_new_pin(self):
        self.manager.run("billing", "work", self.project)
        def started(request):
            record = self.manager.record("billing")
            transcript = next((request["config"] / "projects").rglob(record["session_id"] + ".jsonl"))
            private_json(self.manager.folder("billing") / "active.json", {
                "launch_id": record["launch_id"], "session_id": record["session_id"],
                "transcript": str(transcript), "project": str(self.project)})
        self.fake.after_start = started
        self.fake.exit_code = 130
        self.manager.resume("billing", account="lab")
        self.assertEqual(self.manager.record("billing")["profile"], "lab")
        self.fake.exit_code = 0
        self.manager.resume("billing")
        self.assertEqual(self.fake.requests[-1]["token"], "invented-lab")

    def test_shared_crash_keeps_credential_dependency_until_cleanup(self):
        self.manager.run("billing", "work", self.project)
        record = self.manager.record("billing")
        record.update(mode="shared", profile=None, identity=None, retained_profile="work")
        self.manager.write(record)
        def running(request):
            self.assertEqual(self.manager.dependencies("work"), ["billing"])
        self.fake.after_start = running
        self.manager.resume("billing")
        self.assertEqual(self.manager.dependencies("work"), [])

    def test_failed_repin_from_shared_scrubs_new_private_login(self):
        self.manager.run("billing", "work", self.project)
        self.manager.resume("billing", shared=True)
        for failure in ("status_failure", "launch_failure"):
            setattr(self.fake, failure, True)
            with self.assertRaises((RuntimeError, OSError)):
                self.manager.resume("billing", account="lab")
            setattr(self.fake, failure, False)
            self.assertEqual(self.manager.record("billing")["mode"], "shared")
            self.assertFalse((self.manager.folder("billing") / "config" / ".credentials.json").exists())

    def test_enrollment_rechecks_identity_after_acquiring_lock(self):
        @contextmanager
        def concurrent_change(folder):
            self.vault.put("profile/work", profile("personal"))
            yield
        self.fake.login_profile = profile("work")
        with patch("claude_sessions.operation_lock", concurrent_change):
            with self.assertRaisesRegex(RuntimeError, "different account"):
                self.manager.enroll("work", renew=True)
        self.assertEqual(self.vault.get("profile/work")["account"], profile("personal")["account"])

    def test_renewal_token_and_generation_are_one_atomic_write(self):
        self.fake.login_profile = profile("work", expiry=4002444800000)
        writes = []
        real_put = self.vault.put
        def only_profile(key, value):
            writes.append(key)
            if not key.startswith("profile/"):
                raise OSError("A separate generation write is not atomic")
            real_put(key, value)
        with patch.object(self.vault, "put", only_profile):
            self.manager.enroll("work", renew=True)
        self.assertEqual(writes, ["profile/work"])
        self.assertTrue(self.vault.get("profile/work").get("generation"))
        before = self.vault.get("profile/work")
        with patch.object(self.vault, "put", side_effect=OSError("invented store failure")):
            with self.assertRaises(OSError):
                self.manager.enroll("work", renew=True)
        self.assertEqual(self.vault.get("profile/work"), before)

    def test_listing_reports_running_login_mismatch(self):
        def login(request):
            private_json(request["settings"], {"oauthAccount": profile("lab")["account"]})
            private_json(request["config"] / ".credentials.json", {"claudeAiOauth": profile("lab")["oauth"]})
            self.assertIn("identity-changed", self.manager.listing()[0]["state"])
        self.fake.after_start = login
        self.manager.run("billing", "work", self.project)

    def test_switch_to_shared_removes_private_login_and_dependency(self):
        self.manager.run("billing", "work", self.project)
        self.manager.resume("billing", shared=True)
        self.assertEqual(self.manager.dependencies("work"), [])
        self.assertFalse((self.manager.folder("billing") / "config" / ".credentials.json").exists())

    def test_lifecycle_event_tracks_native_conversation_change(self):
        import uuid
        self.manager.run("billing", "work", self.project)
        record = self.manager.record("billing")
        sid = str(uuid.uuid4())
        transcript = self.manager.folder("billing") / "config" / "projects" / "test-project" / (sid + ".jsonl")
        transcript.write_text(json.dumps({"sessionId": sid, "type": "user", "message": {"content": "new conversation"}}) + "\n", encoding="utf-8")
        private_json(self.manager.folder("billing") / "active.json", {"launch_id": record.get("launch_id"), "session_id": sid,
                     "transcript": str(transcript), "project": str(self.project)})
        self.manager.resume("billing")
        self.assertEqual(self.manager.record("billing")["session_id"], sid)
        self.assertEqual(self.fake.requests[-1]["history"], 2)

    def test_start_hook_placeholder_does_not_replace_existing_transcript(self):
        self.manager.run("billing", "work", self.project)
        record = self.manager.record("billing")
        real = record["transcript"]
        ghost = self.manager.folder("billing") / "config" / "projects" / "not-created" / (record["session_id"] + ".jsonl")
        private_json(self.manager.folder("billing") / "active.json", {"launch_id": record["launch_id"],
                     "session_id": record["session_id"], "transcript": str(ghost), "project": str(self.project)})
        self.manager.transcript(record)
        self.assertEqual(record["transcript"], real)

    def test_duplicate_import_refused_and_archived_history_can_be_imported(self):
        self.manager.run("billing", "work", self.project)
        source = self.manager.record("billing")["transcript"]
        with self.assertRaisesRegex(RuntimeError, "already"):
            self.manager.run("duplicate", "lab", self.project, source=source)
        self.manager.delete("billing", confirm=True)
        self.manager.run("restored", "lab", self.project, source=source)
        self.assertEqual(self.fake.requests[-1]["history"], 2)

    def test_import_reconciles_native_session_change_while_chat_is_running(self):
        import uuid
        def native_resume(request):
            record = self.manager.record("billing")
            sid = str(uuid.uuid4())
            source = request["config"] / "projects" / "test-project" / (sid + ".jsonl")
            source.write_text(json.dumps({"sessionId": sid}) + "\n", encoding="utf-8")
            private_json(self.manager.folder("billing") / "active.json", {
                "launch_id": record["launch_id"], "session_id": sid,
                "transcript": str(source), "project": str(self.project)})
            with self.assertRaisesRegex(RuntimeError, "already|belongs"):
                self.manager.run("duplicate", "lab", self.project, source=source)
        self.fake.after_start = native_resume
        self.manager.run("billing", "work", self.project)

    def test_wrapper_resume_refuses_duplicate_identity_from_native_resume(self):
        self.manager.run("billing", "work", self.project)
        self.manager.run("docs", "lab", self.project)
        self.manager.resume("billing", shared=True)
        self.manager.resume("docs", shared=True)
        billing = self.manager.record("billing")
        docs = self.manager.record("docs")
        private_json(self.manager.folder("billing") / "active.json", {
            "launch_id": billing["launch_id"], "session_id": docs["session_id"],
            "transcript": docs["transcript"], "project": str(self.project)})
        before = len(self.fake.requests)
        for name in ("billing", "docs"):
            with self.assertRaisesRegex(RuntimeError, "already|claimed"):
                self.manager.resume(name)
        self.assertEqual(len(self.fake.requests), before)
        self.manager.delete("billing", confirm=True)
        self.manager.resume("docs")

    def test_account_backend_and_settings_auth_conflicts_fail_before_launch(self):
        config = obj(self.home / ".claude.json")
        config["cachedGrowthBookFeatures"] = {"tengu_windows_credman": True}
        private_json(self.home / ".claude.json", config)
        with self.assertRaisesRegex(RuntimeError, "backend"):
            self.manager.run("blocked", "work", self.project)
        self.set_default("personal")
        for settings in ({"disableAllHooks": True}, {"env": {"ANTHROPIC_API_KEY": "invented-override"}}):
            private_json(self.home / ".claude" / "settings.json", settings)
            with self.assertRaises(RuntimeError):
                self.manager.run("blocked", "work", self.project)
        self.assertEqual(self.fake.requests, [])

    def test_shared_launch_does_not_duplicate_user_hooks(self):
        private_json(self.home / ".claude" / "settings.json", {
            "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "example-user-hook"}]}]}})
        self.manager.run("billing", "work", self.project)
        settings = obj(self.manager.folder("billing") / "launch-settings.json")
        self.assertEqual(len(settings["hooks"]["SessionStart"]), 2)
        self.manager.resume("billing", shared=True)
        settings = obj(self.manager.folder("billing") / "launch-settings.json")
        self.assertEqual(len(settings["hooks"]["SessionStart"]), 1)


if __name__ == "__main__":
    unittest.main()
