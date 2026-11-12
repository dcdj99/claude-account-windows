"""Private Claude login preparation and isolated account enrollment."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

from claude_account import identity, label, obj
from claude_account_windows import operation_lock, private_directory, private_json, reject_links

AUTH_OVERRIDES = ("CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN",
                 "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_BASE_URL",
                 "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                 "CLAUDE_CODE_FORCE_WINDOWS_CREDMAN", "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR",
                 "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR", "CLAUDE_CODE_SKIP_PROMPT_HISTORY")


def validate_environment(env):
    keys = {k.upper(): v for k, v in env.items()}
    if any(keys.get(k) not in (None, "", "0") for k in AUTH_OVERRIDES):
        raise RuntimeError("This shell has an authentication or profile override. Use an ordinary PowerShell window.")


def invoke_claude(args, *, env, cwd, capture=False, lease=None):
    options = dict(env=env, cwd=cwd)
    if capture:
        return subprocess.run(args, **options, capture_output=True, text=True, encoding="utf-8", timeout=30)
    if lease:
        startup = subprocess.STARTUPINFO()
        os.set_handle_inheritable(lease.handle, True)
        startup.lpAttributeList = {"handle_list": [lease.handle]}
        options.update(startupinfo=startup, close_fds=True)
    try:
        child = subprocess.Popen(args, **options)
    finally:
        if lease:
            os.set_handle_inheritable(lease.handle, False)
    # Console Ctrl+C also reaches Claude. Keep ownership until its process exits.
    while True:
        try:
            return subprocess.CompletedProcess(args, child.wait())
        except KeyboardInterrupt:
            continue


def snapshot(config, settings=None):
    account = obj(settings or config / ".claude.json", True).get("oauthAccount")
    oauth = obj(config / ".credentials.json", True).get("claudeAiOauth")
    if not identity(account) or not isinstance(oauth, dict) or not oauth.get("accessToken"):
        return None
    return {"account": account, "oauth": oauth}


class AccountRuntime:
    def __init__(self, home, root, vault, cli, invoke=invoke_claude):
        self.home, self.root = Path(home), Path(root)
        self.vault, self.cli, self.invoke = vault, cli, invoke
        self.shared = self.home / ".claude"

    def profile(self, name):
        label(name)
        saved = self.vault.get("profile/" + name)
        if not saved or not identity(saved.get("account")) or not saved.get("oauth", {}).get("accessToken"):
            raise RuntimeError("Unknown or incomplete saved account '" + name + "'. Run 'claude-account list'.")
        return saved

    def environment(self, config=None):
        env = dict(os.environ)
        validate_environment(env)
        if config is not None:
            env["CLAUDE_CONFIG_DIR"] = str(config)
        return env

    def check_settings(self, project):
        shared_config = obj(self.home / ".claude.json", True)
        if shared_config.get("cachedGrowthBookFeatures", {}).get("tengu_windows_credman"):
            raise RuntimeError("Claude's alternate Credential Manager backend is enabled. File-backed managed logins are unavailable.")
        project = Path(project).resolve()
        folders = [self.shared, *(parent / ".claude" for parent in (project, *project.parents))]
        files = list(dict.fromkeys(folder / name for folder in folders for name in ("settings.json", "settings.local.json")))
        for path in files:
            settings = obj(path, True)
            env = settings.get("env", {})
            if not isinstance(env, dict):
                raise RuntimeError("Invalid settings environment in " + str(path))
            validate_environment(env)
            if settings.get("apiKeyHelper") or settings.get("forceLoginMethod") == "console":
                raise RuntimeError("Settings select another authentication method: " + str(path))

    def seed(self, config, target):
        private_directory(config)
        current = obj(config / ".claude.json", True)
        if not current:
            current = obj(self.home / ".claude.json", True)
            for key in ("oauthAccount", "cachedGrowthBookFeatures", "cachedStatsigGates", "cachedExtraUsageEnabled"):
                current.pop(key, None)
            # Include user extensions/instructions, never share mutable login or transcript paths.
            for name in ("CLAUDE.md", "skills", "commands", "agents", "hooks"):
                src, dest = self.shared / name, config / name
                if src.is_file():
                    shutil.copy2(src, dest)
                elif src.is_dir():
                    shutil.copytree(src, dest, dirs_exist_ok=True)
            for name in ("installed_plugins.json", "known_marketplaces.json", "blocklist.json"):
                src = self.shared / "plugins" / name
                if src.is_file():
                    private_directory(config / "plugins")
                    shutil.copy2(src, config / "plugins" / name)
        current["oauthAccount"] = target["account"]
        current["hasCompletedOnboarding"] = True
        if current.get("cachedGrowthBookFeatures", {}).get("tengu_windows_credman"):
            raise RuntimeError("This chat uses Claude's alternate Credential Manager backend; file-backed login changes are unavailable.")
        private_json(config / ".claude.json", current)
        credentials = obj(config / ".credentials.json", True)
        credentials["claudeAiOauth"] = target["oauth"]
        private_json(config / ".credentials.json", credentials)

    def verify(self, config, target, project):
        env = self.environment(config)
        result = self.invoke([self.cli, "auth", "status", "--json"], env=env, cwd=project, capture=True)
        try:
            status = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise RuntimeError("Claude could not verify the account; the chat was not started.") from None
        expected = config or self.shared
        if (result.returncode not in (0, 1) or not status.get("loggedIn") or status.get("authMethod") != "claude.ai"
                or Path(status.get("configDirectory") or "").resolve() != expected.resolve()):
            raise RuntimeError("Claude could not verify this subscription login. Renew the saved account first.")
        if target and ((status.get("email") or "").casefold() != target["account"]["emailAddress"].casefold()
                       or (status.get("orgId") or None) != (target["account"].get("organizationUuid") or None)):
            raise RuntimeError("Claude reported a different account identity. The chat was not started.")
        return env

    def _latest(self, target, epoch=None):
        candidates = [target]
        live = snapshot(self.shared, self.home / ".claude.json")
        if not epoch and live and identity(live["account"]) == identity(target["account"]):
            candidates.append(live)
        return max(candidates, key=lambda item: item["oauth"].get("expiresAt") or 0)

    def enroll(self, name, renew=False, sso=False):
        label(name)
        auth = self.root / "enrollment" / uuid.uuid4().hex
        private_directory(auth)
        try:
            env = self.environment(auth)
            args = [self.cli, "auth", "login", "--claudeai"] + (["--sso"] if sso else [])
            with operation_lock(self.shared):
                previous = self.vault.get("profile/" + name)
                if renew and not previous:
                    raise RuntimeError("Unknown saved account. Use 'add NAME --isolated' first.")
                if self.invoke(args, env=env, cwd=self.home).returncode:
                    raise RuntimeError("Browser sign-in did not finish; saved account unchanged.")
                candidate = snapshot(auth)
                if not candidate:
                    raise RuntimeError("Browser sign-in did not provide a complete subscription account.")
                self.verify(auth, candidate, self.home)
                if previous and identity(previous["account"]) != identity(candidate["account"]):
                    raise RuntimeError("You signed into a different account identity. Saved account unchanged.")
                for other in self.vault.names():
                    if other != name and identity(self.profile(other)["account"]) == identity(candidate["account"]):
                        raise RuntimeError("This account is already saved as '" + other + "'.")
                candidate["generation"] = uuid.uuid4().hex
                self.vault.put("profile/" + name, candidate)
            print("Saved " + name + " through isolated sign-in; shared default unchanged.")
        finally:
            reject_links(auth)
            shutil.rmtree(auth)
