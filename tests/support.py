"""Invented account data and local authentication fixture."""
import json
from pathlib import Path
import subprocess
from claude_account import obj, private_json


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
        raise AssertionError("Unexpected native command in authentication fixture")
