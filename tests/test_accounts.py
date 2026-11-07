"""Windows-only switch tests using invented account data and a disposable store."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from claude_account import Accounts, private_json


class MemoryVault:
    def __init__(self):
        self.records = {}

    def get(self, key):
        return copy.deepcopy(self.records.get(key))

    def put(self, key, value):
        self.records[key] = copy.deepcopy(value)

    def delete(self, key):
        self.records.pop(key, None)

    def names(self):
        return sorted(key.split("/", 1)[1] for key in self.records if key.startswith("profile/"))


class AccountTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.manager = Accounts(Path(root.name), MemoryVault(), "unused")
        self.manager.config.mkdir()
        replacement = patch.object(self.manager, "status", side_effect=self.fixture_status)
        replacement.start()
        self.addCleanup(replacement.stop)

    def login(self, name):
        account = {"accountUuid": "example-" + name, "emailAddress": name + "@example.invalid",
                   "organizationUuid": "example-org"}
        self.manager.settings.write_text(json.dumps({"oauthAccount": account, "theme": "dark"}), encoding="utf8")
        self.manager.creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "invented-" + name},
                                                 "mcpOAuth": {"example": "keep"}}), encoding="utf8")

    def fixture_status(self):
        account = json.loads(self.manager.settings.read_text(encoding="utf8"))["oauthAccount"]
        return {"loggedIn": True, "authMethod": "claude.ai", "email": account["emailAddress"],
                "orgId": account["organizationUuid"]}

    def test_switch_between_three_profiles_preserves_other_fields(self):
        for name in ("work", "personal", "lab"):
            self.login(name)
            self.manager.save(name)
        for name in ("work", "personal", "lab", "work"):
            self.manager.use(name)
            self.assertEqual(self.fixture_status()["email"], name + "@example.invalid")
            self.assertEqual(json.loads(self.manager.creds.read_text(encoding="utf8")),
                             {"claudeAiOauth": {"accessToken": "invented-" + name}, "mcpOAuth": {"example": "keep"}})
            self.assertEqual(json.loads(self.manager.settings.read_text(encoding="utf8"))["theme"], "dark")

    def test_direct_login_needs_a_name_and_cannot_replace_another_identity(self):
        self.login("work")
        self.manager.save("work")
        self.login("personal")
        with self.assertRaisesRegex(RuntimeError, "unsaved"):
            self.manager.use("work")
        with self.assertRaisesRegex(RuntimeError, "another account"):
            self.manager.save("work")
        self.assertEqual(self.fixture_status()["email"], "personal@example.invalid")
        self.manager.save("personal")
        self.manager.use("work")

    def test_interrupted_switch_resumes(self):
        self.login("work")
        self.manager.save("work")
        self.login("lab")
        self.manager.save("lab")

        def fail_credential_write(path, value):
            if path == self.manager.creds:
                raise OSError("simulated interruption")
            private_json(path, value)

        with patch("claude_account.private_json", side_effect=fail_credential_write):
            with self.assertRaisesRegex(OSError, "simulated interruption"):
                self.manager.use("work")
        self.assertEqual(self.manager.vault.get("pending")["name"], "work")
        self.manager.use("work")
        self.assertIsNone(self.manager.vault.get("pending"))
        self.assertEqual(self.fixture_status()["email"], "work@example.invalid")


if __name__ == "__main__":
    unittest.main()
