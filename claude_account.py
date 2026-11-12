"""Saved Claude accounts and isolated launches using native Claude conversations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_account_windows import Vault, locked, operation_lock, private_json


def label(value):
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", value):
        raise argparse.ArgumentTypeError("Use a lowercase name such as work, personal or lab.")
    return value


def obj(path, optional=False):
    try:
        record = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        if optional:
            return {}
        raise
    if not isinstance(record, dict):
        raise ValueError(f"Invalid JSON object in {path.name}; no update was made.")
    return record


def identity(account):
    if not isinstance(account, dict):
        return None
    if not account.get("accountUuid") or not account.get("emailAddress"):
        return None
    return (account["accountUuid"], account.get("organizationUuid"))


def digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf8")
    return hashlib.sha256(encoded).hexdigest()


class Accounts:
    def __init__(self, home, vault, cli):
        self.config = home / ".claude"
        self.settings = home / ".claude.json"
        self.creds = self.config / ".credentials.json"
        self.vault, self.cli = vault, cli

    def status(self):
        run = subprocess.run([self.cli, "auth", "status", "--json"], capture_output=True,
                             text=True, encoding="utf8", timeout=20)
        result = json.loads(run.stdout)
        if run.returncode not in (0, 1) or not isinstance(result.get("loggedIn"), bool):
            raise RuntimeError("Could not read Claude's current authentication status.")
        if Path(result.get("configDirectory") or "").resolve() != self.config.resolve():
            raise RuntimeError("Use an ordinary terminal with the default Claude configuration folder.")
        if result.get("authMethod") not in ("none", "claude.ai"):
            raise RuntimeError("Claude is using another authentication method. This tool switches claude.ai subscriptions.")
        return result

    def check_files(self):
        if not self.config.is_dir() or (self.config / ".config.json").exists():
            raise RuntimeError("This installation does not use the standard Windows Claude configuration layout.")
        for path in (self.config, self.settings, self.creds):
            if path.exists() and (path.lstat().st_file_attributes & 0x400):
                raise RuntimeError("Linked or redirected Claude login files are not supported.")

    def snapshot(self, status):
        settings = obj(self.settings)
        if settings.get("cachedGrowthBookFeatures", {}).get("tengu_windows_credman"):
            raise RuntimeError("Claude's alternate Credential Manager backend is enabled; the file-backed switch is unavailable.")
        account = settings.get("oauthAccount")
        oauth = obj(self.creds, optional=True).get("claudeAiOauth")
        if not status["loggedIn"] and not oauth:
            return None
        if not isinstance(oauth, dict) or not oauth.get("accessToken") or not identity(account):
            raise RuntimeError("Claude reported an incomplete or unsupported saved login. Use Claude's normal login first.")
        if (not status["loggedIn"] or status["authMethod"] != "claude.ai"
                or (status.get("email") or "").casefold() != account["emailAddress"].casefold()
                or (status.get("orgId") or None) != (account.get("organizationUuid") or None)):
            raise RuntimeError("The login changed or its identity does not match Claude's status. Retry after login finishes.")
        return {"account": account, "oauth": oauth}

    def profiles(self):
        return {n: self.vault.get("profile/" + n) for n in self.vault.names()}

    def match(self, snap, saved):
        if snap:
            return next((n for n, p in saved.items() if identity(p["account"]) == identity(snap["account"])), None)
        return None

    def require_clean(self):
        if self.vault.get("pending"):
            raise RuntimeError("A switch was interrupted. Run 'list' and repeat the indicated 'use' command.")

    def remove(self, name):
        pending = self.vault.get("pending")
        if pending and pending["name"] == name:
            raise RuntimeError(f"Finish the interrupted switch with 'claude-account use {name}' before removing it.")
        if name not in self.vault.names():
            raise RuntimeError(f"Unknown profile '{name}'. Run 'claude-account list'.")
        self.vault.delete("profile/" + name)
        print(f"Removed saved profile '{name}' from Windows Credential Manager.")

    def save(self, name):
        status = self.status()
        with locked(self.config, self.settings):
            self.require_clean()
            snap = self.snapshot(status)
            if not snap:
                raise RuntimeError("Sign in with Claude first.")
            saved = self.profiles()
            other = self.match(snap, saved)
            if name in saved and identity(saved[name]["account"]) != identity(snap["account"]):
                raise RuntimeError(f"'{name}' belongs to another account. Use a different label.")
            if other and other != name:
                raise RuntimeError(f"This account is already saved as '{other}'.")
            self.vault.put("profile/" + name, snap)
        print(f"Saved {name}: {snap['account']['emailAddress']}")

    def checkpoint(self, snap, saved):
        if snap:
            active = self.match(snap, saved)
            if not active:
                raise RuntimeError("Your current Claude login is unsaved. Run 'claude-account save NAME' before switching away.")
            self.vault.put("profile/" + active, snap)
            return active
        return None

    def add(self, name, sso=False):
        # Do not hold Claude's own locks during a browser sign-in.
        status = self.status()
        with locked(self.config, self.settings):
            self.require_clean()
            self.checkpoint(self.snapshot(status), self.profiles())
        print(f"Select the account for '{name}' in Claude's browser login.", flush=True)
        args = [self.cli, "auth", "login", "--claudeai"]
        if sso:
            args.append("--sso")
        if subprocess.run(args).returncode:
            raise RuntimeError("Browser login did not finish. Check 'claude-account list' for the active login.")
        self.save(name)

    def finish(self, pending, target):
        # Only finish the exact interrupted change. Do not overwrite a newer direct browser login.
        config = obj(self.settings)
        credentials = obj(self.creds, optional=True)
        new_id, new_login = target["account"], target["oauth"]
        if [digest(new_id), digest(new_login)] != pending["after"]:
            raise RuntimeError("The pending target changed. Use 'recover-login' to sign in again.")
        seen = [digest(config.get("oauthAccount")), digest(credentials.get("claudeAiOauth"))]
        if any(actual not in (before, after) for actual, before, after in zip(seen, pending["before"], pending["after"])):
            raise RuntimeError("A separate login or refresh changed the live state. Use 'recover-login' to reconcile it.")
        if seen[0] != pending["after"][0]:
            config["oauthAccount"] = new_id
            private_json(self.settings, config)
        if seen[1] != pending["after"][1]:
            credentials["claudeAiOauth"] = new_login
            private_json(self.creds, credentials)
        if [digest(obj(self.settings).get("oauthAccount")), digest(obj(self.creds).get("claudeAiOauth"))] != pending["after"]:
            raise RuntimeError("The files changed during the switch. Run 'list' for recovery instructions.")
        self.vault.delete("pending")

    def use(self, name):
        status = self.status()
        with locked(self.config, self.settings):
            saved = self.profiles()
            if name not in saved:
                raise RuntimeError(f"Unknown profile '{name}'. Run 'claude-account list'.")
            pending = self.vault.get("pending")
            if pending and pending["name"] != name:
                raise RuntimeError(f"Finish the interrupted switch with 'claude-account use {pending['name']}'.")
            if not pending:
                snap = self.snapshot(status)
                current = self.checkpoint(snap, saved)
                if current == name:
                    print(f"{name} is already the shared default.")
                    return
                target = saved[name]
                if not target["oauth"].get("accessToken") or not identity(target["account"]):
                    raise RuntimeError("Saved login is incomplete. Renew it with Claude's browser login.")
                pending = {"name": name, "before": [digest(obj(self.settings).get("oauthAccount")),
                                                     digest(obj(self.creds, optional=True).get("claudeAiOauth"))],
                           "after": [digest(target["account"]), digest(target["oauth"])]}
                self.vault.put("pending", pending)
            self.finish(pending, saved[name])
        confirmed = self.status()
        account = saved[name]["account"]
        if (not confirmed["loggedIn"] or confirmed["authMethod"] != "claude.ai"
                or (confirmed.get("email") or "").casefold() != account["emailAddress"].casefold()
                or (confirmed.get("orgId") or None) != (account.get("organizationUuid") or None)):
            raise RuntimeError("Claude did not confirm the selected login. Check 'claude-account list'.")
        print(f"Shared default: {name} ({account['emailAddress']}). Existing lanes pick it up on their next turn.")

    def recover(self):
        pending = self.vault.get("pending")
        if not pending:
            print("No interrupted switch.")
            return
        print("Claude will open its normal browser login. Sign in to the account you want active.", flush=True)
        if subprocess.run([self.cli, "auth", "login", "--claudeai"]).returncode:
            raise RuntimeError("Recovery sign-in did not complete.")
        status = self.status()
        with locked(self.config, self.settings):
            if not self.snapshot(status) or self.vault.get("pending") != pending:
                raise RuntimeError("The login or pending operation changed. Check 'list'.")
            self.vault.delete("pending")
        print("The browser login is active. Use 'list' to see it, then 'save NAME' if needed.")

    def listing(self):
        status = self.status()
        pending = self.vault.get("pending")
        if pending:
            print(f"Interrupted switch. Run 'claude-account use {pending['name']}' to finish.")
        email = status.get("email")
        print("Claude reports:", (email or "signed out"), status.get("orgName") or "")
        saved = self.profiles()
        matched = False
        for name, p in saved.items():
            account = p["account"]
            active = (not pending and email and email.casefold() == account["emailAddress"].casefold()
                      and (status.get("orgId") or None) == (account.get("organizationUuid") or None))
            matched = matched or bool(active)
            print(f"  {'*' if active else ' '} {name:<16} {account['emailAddress']}  {account.get('organizationName') or ''}")
        if not saved:
            print("No saved profiles yet. Name your current login with 'claude-account save work'.")
        elif email and not matched and not pending:
            print("This login has no saved name. Run 'claude-account save NAME' to keep it.")
        return list(saved)


def make_shortcut(name):
    script = str(Path(__file__).resolve()).replace("%", "%%")
    python = str(Path(sys.executable).resolve()).replace("%", "%%")
    file = Path.cwd() / f"Use Claude {name}.cmd"
    body = f'@echo off\r\nsetlocal DisableDelayedExpansion\r\n"{python}" -I "{script}" use {name}\r\nif errorlevel 1 pause\r\n'
    with file.open("x", encoding="mbcs", newline="") as f:
        f.write(body)
    print(f"Double-click: {file}")


def install():
    """Install a standalone copy and a command in the existing per-user PATH folder."""
    src = Path(__file__).resolve().parent
    files = {name: (src / name).read_bytes() for name in ("claude_account.py", "claude_account_windows.py", "claude_sessions.py", "claude_account_hook.py", "claude_native.py")}
    version = hashlib.sha256(b"\0".join(files.values())).hexdigest()[:16]
    relative = Path("Programs") / "ClaudeAccountCLI" / version
    target = Path(os.environ["LOCALAPPDATA"]) / relative
    bin_dir = Path.home() / ".local/bin"
    if not bin_dir.is_dir() or bin_dir.resolve() not in [Path(p.strip('"')).resolve() for p in os.get_exec_path() if p]:
        raise RuntimeError("The per-user .local/bin folder is not on PATH. Use the portable command in this folder.")
    launcher = bin_dir / "claude-account.cmd"
    marker = b"@rem Claude Account CLI managed launcher\r\n"
    if launcher.exists() and not launcher.read_bytes().startswith(marker):
        raise RuntimeError("An unrelated claude-account.cmd already exists. The portable command is available.")
    target.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        dest = target / name
        if not dest.exists():
            with dest.open("xb") as out:
                out.write(content)
        if dest.read_bytes() != content:
            raise RuntimeError("Installation directory contains a different file. The command was not changed.")
    script = str(target / "claude_account.py").replace("%", "%%")
    python = str(Path(sys.executable).resolve()).replace("%", "%%")
    command = (f'@echo off\r\nsetlocal DisableDelayedExpansion\r\n"{python}" -I -B "{script}" %*\r\n'
               'exit /b %errorlevel%\r\n').encode("mbcs")
    with tempfile.NamedTemporaryFile(dir=bin_dir, prefix=".claude-account-", delete=False) as tmp:
        tmp.write(marker + command)
    staged = Path(tmp.name)
    try:
        os.replace(staged, launcher) if launcher.exists() else staged.rename(launcher)
    finally:
        staged.unlink(missing_ok=True)
    print(f"Installed {launcher}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    cmds = parser.add_subparsers(dest="action")
    for cmd, desc in (("save", "Name or refresh your current Claude login."),
                      ("add", "Sign in as another account through Claude and save it."),
                      ("use", "Make a saved account the shared Claude default."),
                      ("remove", "Delete a saved profile from Windows Credential Manager."),
                      ("shortcut", "Create a one-click switch in the current folder.")):
        sub = cmds.add_parser(cmd, help=desc)
        sub.add_argument("name", type=label)
        if cmd == "add":
            sub.add_argument("--sso", action="store_true", help="Use Claude's enterprise SSO flow.")
            sub.add_argument("--isolated", action="store_true", help="Save the login without changing the shared default.")
    cmds.add_parser("list", help="Show saved accounts and Claude's current login.")
    cmds.add_parser("install", help="Make claude-account available from any terminal for this Windows user.")
    cmds.add_parser("recover-login", help="Recover from an interrupted switch followed by an external login.")
    run = cmds.add_parser("run", help="Launch native Claude with a saved account; native options follow --.")
    run.add_argument("account", type=label)
    run.add_argument("--name", help="Compatibility alias for native --name after the separator.")
    run.add_argument("--resume", metavar="SESSION", help="Compatibility alias for native --resume after the separator.")
    resume = cmds.add_parser("resume", help="Compatibility shortcut for native resume; select --account or --shared.")
    resume.add_argument("name")
    mode = resume.add_mutually_exclusive_group()
    mode.add_argument("--account", type=label, help="Resume using this saved account for this launch.")
    mode.add_argument("--shared", action="store_true", help="Follow the current and future shared defaults.")
    sessions_cmd = cmds.add_parser("sessions", help="Show running account launches; Claude's --resume shows history.")
    sessions_cmd.add_argument("--legacy", action="store_true", help="Inspect the older managed-chat records for migration.")
    cmds.add_parser("migrate-sessions", help="Make stopped legacy chats visible in native history; retain originals.")
    renew = cmds.add_parser("renew", help="Renew a saved account through isolated browser login.")
    renew.add_argument("name", type=label)
    renew.add_argument("--sso", action="store_true")
    delete = cmds.add_parser("delete-session", help="Clean an old managed-chat record; native history is unaffected.")
    delete.add_argument("name", type=label)
    delete.add_argument("--history", action="store_true", help="Also remove history in this chat's private directory.")
    delete.add_argument("--yes", action="store_true", help="Confirm the selected deletion.")
    argv = sys.argv[1:]
    split = argv.index("--") if "--" in argv else len(argv)
    opts = parser.parse_args(argv[:split])
    extra = argv[split + 1:]
    if extra and opts.action not in ("run", "resume"):
        parser.error("Claude options after -- are only accepted by run and resume.")
    if opts.action == "install":
        install()
        return
    overrides = ["CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY",
                 "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                 "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_FORCE_WINDOWS_CREDMAN"]
    if any(os.environ.get(key) not in (None, "", "0") for key in overrides):
        raise RuntimeError("Open an ordinary PowerShell window using Claude's shared subscription login. This shell has an authentication or profile override.")
    cli = shutil.which("claude.exe")
    if not cli:
        raise RuntimeError("claude.exe is not on PATH.")
    manager = Accounts(Path.home(), Vault(), cli)
    from claude_sessions import Sessions, validate_environment
    from claude_native import NativeLauncher
    validate_environment(os.environ)
    sessions = Sessions(Path.home(), Path(os.environ["LOCALAPPDATA"]) / "ClaudeAccountCLI" / "data", manager.vault, cli)
    native = NativeLauncher(Path.home(), sessions.root, manager.vault, cli)
    if opts.action == "sessions":
        if opts.legacy:
            sessions.listing()
        else:
            native.reap()
            native.active(display=True)
        return
    if opts.action == "migrate-sessions":
        native.reap()
        native.migrate_legacy()
        print("Stopped legacy chats migrated to native history. Originals retained; running legacy chats skipped.")
        return
    if opts.action == "run":
        forwarded = (["--name", opts.name] if opts.name else []) + (["--resume", opts.resume] if opts.resume else []) + extra
        return native.run(opts.account, Path.cwd(), forwarded)
    if opts.action == "resume":
        return native.resume_alias(opts.name, account=opts.account, shared=opts.shared, args=extra)
    if opts.action == "delete-session":
        sessions.delete(opts.name, history=opts.history, confirm=opts.yes)
        return
    if opts.action == "renew" or (opts.action == "add" and opts.isolated):
        sessions.enroll(opts.name, renew=opts.action == "renew", sso=opts.sso)
        return
    if opts.action in ("list", None):
        names = manager.listing()
        if opts.action is None and names:
            selected = input("Profile to activate (Enter to cancel): ").strip()
            if selected:
                manager.check_files()
                with operation_lock(manager.config):
                    manager.use(label(selected))
        return
    manager.check_files()
    if opts.action == "shortcut":
        if opts.name not in manager.vault.names():
            raise RuntimeError("Save that profile first.")
        make_shortcut(opts.name)
        return
    if opts.action == "remove":
        native.reap()
    with operation_lock(manager.config):
        if opts.action == "add":
            manager.add(opts.name, opts.sso)
        elif opts.action == "save":
            manager.save(opts.name)
        elif opts.action == "use":
            manager.use(opts.name)
        elif opts.action == "remove":
            if any(r["profile"] == opts.name for r in native.active()):
                raise RuntimeError("That account has a running launch. Exit it before removing the saved account.")
            dependent = sessions.dependencies(opts.name)
            if dependent:
                raise RuntimeError("Saved account is retained by managed chats: " + ", ".join(dependent) +
                                   ". Switch their pins or delete their managed logins before removing it.")
            manager.remove(opts.name)
        elif opts.action == "recover-login":
            manager.recover()


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired, argparse.ArgumentTypeError) as error:
        print(f"Stopped: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
