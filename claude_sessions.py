"""Named foreground Claude chats with independent subscription login storage."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from claude_account import digest, identity, label, obj
from claude_account_windows import (SessionLease, operation_lock, private_directory,
                                    private_json, reject_links)

AUTH_OVERRIDES = ("CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN",
                 "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_BASE_URL",
                 "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                 "CLAUDE_CODE_FORCE_WINDOWS_CREDMAN", "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR",
                 "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR", "CLAUDE_CODE_SKIP_PROMPT_HISTORY")
PERSISTENT_OPTIONS = {"--model", "--effort", "--permission-mode", "--agent", "--add-dir"}
TRANSIENT_OPTIONS = {"--output-format", "--input-format", "--max-turns", "--max-budget-usd"}
SWITCHES = {"-p", "--print", "--verbose", "--debug"}


def validate_environment(env):
    keys = {k.upper(): v for k, v in env.items()}
    if any(keys.get(k) not in (None, "", "0") for k in AUTH_OVERRIDES):
        raise RuntimeError("This shell has an authentication or profile override. Use an ordinary PowerShell window.")


def checked_args(args):
    args = list(args)
    persistent, index, prompts = [], 0, 0
    while index < len(args):
        value = args[index]
        if value in PERSISTENT_OPTIONS | TRANSIENT_OPTIONS:
            if index + 1 >= len(args) or args[index + 1].startswith("-"):
                raise RuntimeError("Missing value for " + value)
            if value in PERSISTENT_OPTIONS:
                persistent.extend(args[index:index + 2])
            if value == "--permission-mode" and args[index + 1] == "bypassPermissions":
                raise RuntimeError("Managed sessions do not enable permission bypass.")
            index += 2
        elif value in SWITCHES:
            index += 1
        elif value.startswith("-"):
            raise RuntimeError("Unsupported managed-session option: " + value)
        else:
            prompts += 1
            if prompts > 1 or value in {"auth", "setup-token", "update", "install", "mcp", "agents", "attach"}:
                raise RuntimeError("Pass one prompt, not a Claude subcommand. Use managed commands for account changes.")
            index += 1
    return args, persistent


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


class Sessions:
    def __init__(self, home, root, vault, cli, invoke=invoke_claude):
        self.home, self.root = Path(home), Path(root)
        self.vault, self.cli, self.invoke = vault, cli, invoke
        self.shared = self.home / ".claude"

    def folder(self, name):
        return self.root / "sessions" / label(name)

    def record(self, name):
        folder = self.folder(name)
        reject_links(folder)
        if not (folder / "session.json").is_file():
            raise RuntimeError("Unknown managed chat '" + name + "'. Run 'claude-account sessions'.")
        data = obj(folder / "session.json")
        if data.get("version") != 1 or data.get("name") != name:
            raise RuntimeError("Unsupported or damaged managed chat record.")
        return data

    def write(self, data):
        private_json(self.folder(data["name"]) / "session.json", data)

    def records(self):
        folder = self.root / "sessions"
        if not folder.exists():
            return []
        reject_links(folder)
        return [self.record(p.name) for p in sorted(folder.iterdir()) if p.is_dir() and (p / "session.json").exists()]

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
            if settings.get("disableAllHooks"):
                raise RuntimeError("Managed chat tracking requires lifecycle hooks, but settings disable them: " + str(path))

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

    def _latest(self, target, own_config=None, epoch=None, previous_epoch=None):
        candidates = [target]
        live = snapshot(self.shared, self.home / ".claude.json")
        if not epoch and live and identity(live["account"]) == identity(target["account"]):
            candidates.append(live)
        if own_config and epoch == previous_epoch:
            local = snapshot(own_config)
            if local and identity(local["account"]) == identity(target["account"]):
                candidates.append(local)
        return max(candidates, key=lambda item: item["oauth"].get("expiresAt") or 0)

    def transcript(self, record):
        active = obj(self.folder(record["name"]) / "active.json", True)
        if active and active.get("launch_id") == record.get("launch_id"):
            path = Path(active.get("transcript", ""))
            root = Path(record["last_config"]) / "projects"
            sid = str(uuid.UUID(active["session_id"]))
            known_source = record.get("transcript") and path.resolve() == Path(record["transcript"]).resolve()
            if path.is_absolute() and (path.resolve().is_relative_to(root.resolve()) or known_source) and path.name == sid + ".jsonl":
                if path.is_file():
                    record["session_id"], record["transcript"] = sid, str(path)
                elif sid != record["session_id"]:
                    record["session_id"], record["transcript"] = sid, None
                if Path(active.get("project", "")).is_dir():
                    record["project"] = active["project"]
        root = Path(record["last_config"]) / "projects"
        paths = list(root.rglob(record["session_id"] + ".jsonl")) if root.exists() else []
        paths = [p for p in paths if "subagents" not in p.parts]
        if len(paths) > 1:
            raise RuntimeError("Multiple transcript matches; refusing to select a conversation implicitly.")
        if paths:
            record["transcript"] = str(paths[0].resolve())
        return record.get("transcript")

    def run(self, name, account, project, args=(), source=None):
        name = name or "chat-" + uuid.uuid4().hex[:10]
        args, persistent = checked_args(args)
        project = Path(project).resolve()
        self.check_settings(project)
        self.profile(account)
        folder = self.folder(name)
        private_directory(folder)
        with ExitStack() as handles:
            with SessionLease(self.root / ".registry"):
                lease = handles.enter_context(SessionLease(folder / ".lease"))
                if (folder / "session.json").exists():
                    raise RuntimeError("Chat name already exists. Use 'resume' or choose a different name.")
                record = dict(version=1, name=name, session_id=str(uuid.uuid4()), project=str(project),
                              mode="pinned", profile=account, identity=None, persistent_args=persistent,
                              transcript=None, last_config=str(folder / "config"), state="ready")
                if source:
                    if not Path(source).is_absolute():
                        raise RuntimeError("Import requires an absolute .jsonl transcript path.")
                    source = Path(source).resolve()
                    if source.suffix != ".jsonl" or not source.is_file():
                        raise RuntimeError("Import requires an existing absolute .jsonl transcript path.")
                    sid = str(uuid.UUID(source.stem))
                    for existing in self.records():
                        if existing.get("state") == "archived":
                            continue
                        self.transcript(existing)
                        owned = self.folder(existing["name"]) / "config" / "projects"
                        if existing["session_id"] == sid or source.is_relative_to(owned.resolve()):
                            raise RuntimeError("This conversation already has a managed chat name.")
                        # A running shared chat can use native /resume anywhere in its history.
                        # Reserve that history scope while it runs, including between hook events.
                        if source.is_relative_to((Path(existing["last_config"]) / "projects").resolve()):
                            with SessionLease(self.folder(existing["name"]) / ".lease"):
                                pass
                    record.update(session_id=sid, transcript=str(source))
                self.write(record)
            return self._launch(record, account, False, args, lease)

    def launch_settings(self, record):
        # Shared mode already loads these settings natively; do not duplicate user hooks.
        settings = obj(self.shared / "settings.json", True) if record["mode"] == "pinned" else {}
        destination = self.folder(record["name"]) / "launch-settings.json"
        hook = {"type": "command", "command": sys.executable,
                "args": ["-I", "-B", str(Path(__file__).with_name("claude_account_hook.py")),
                         str(self.folder(record["name"]) / "active.json"), record["last_config"], record["launch_id"],
                         record.get("transcript") or ""]}
        hooks = settings.setdefault("hooks", {})
        for event in ("SessionStart", "Stop", "SessionEnd", "CwdChanged"):
            hooks.setdefault(event, []).append({"hooks": [hook]})
        private_json(destination, settings)
        return destination

    def resume(self, name, account=None, shared=False, args=()):
        if account and shared:
            raise RuntimeError("Choose a saved account or shared mode, not both.")
        self.record(name)
        with SessionLease(self.folder(name) / ".lease") as lease:
            record = self.record(name)
            if record.get("state") == "archived":
                raise RuntimeError("This chat was deleted from management; its retained transcript can be imported.")
            with SessionLease(self.root / ".registry"):
                self.transcript(record)  # Recover turns saved after a wrapper or machine failure.
                for other in self.records():
                    if other["name"] == name or other.get("state") == "archived":
                        continue
                    self.transcript(other)
                    if other["session_id"] == record["session_id"]:
                        raise RuntimeError("This conversation is also claimed by '" + other["name"] +
                                           "'. Delete one managed name without --history before resuming.")
                self.write(record)
            if record["mode"] == "pinned" and record.get("identity"):
                local = snapshot(self.folder(name) / "config")
                if not local or list(identity(local["account"])) != record["identity"]:
                    record["state"] = "identity-changed"
            if record.get("state") == "identity-changed" and not account and not shared:
                raise RuntimeError("The chat login changed manually. Resume with --account NAME to restore an explicit pin.")
            args, persistent = checked_args(args)
            existing = record.get("persistent_args", [])
            supplied = {args[i] for i in range(len(args)) if args[i] in PERSISTENT_OPTIONS}
            retained = [v for i in range(0, len(existing), 2) if existing[i] not in supplied for v in existing[i:i + 2]]
            record["persistent_args"] = retained + persistent
            return self._launch(record, account or record.get("profile"), shared or (not account and record["mode"] == "shared"),
                                retained + args, lease)

    def _launch(self, record, account, shared, args, lease):
        previous = self.record(record["name"])
        folder = self.folder(record["name"])
        config = None if shared else folder / "config"
        project = Path(record["project"])
        if not project.is_dir():
            raise RuntimeError("The project folder is unavailable: " + str(project))
        self.check_settings(project)
        baseline = target = None
        backup = None
        try:
            with operation_lock(self.shared):
                if not shared:
                    baseline = self.profile(account)
                    epoch = baseline.get("generation")
                    target = self._latest(baseline, config, epoch, record.get("epoch"))
                    if snapshot(config):
                        backup = (obj(config / ".claude.json"), obj(config / ".credentials.json"))
                    self.seed(config, target)
                env = self.verify(config, target, project)
            record.update(mode="shared" if shared else "pinned", profile=None if shared else account,
                          identity=list(identity(target["account"])) if target else None,
                          last_config=str(config or self.shared), state="ready",
                          epoch=epoch if not shared else None,
                          retained_profile=(previous.get("retained_profile") or previous.get("profile")) if shared else None,
                          launch_id=uuid.uuid4().hex)
            env.update(CLAUDE_ACCOUNT_SESSION=record["name"], CLAUDE_ACCOUNT_MODE=record["mode"],
                       CLAUDE_ACCOUNT_PROFILE=account if not shared else "")
            command = [self.cli, "--settings", str(self.launch_settings(record))]
            transcript = record.get("transcript")
            if transcript:
                if not Path(transcript).is_file():
                    raise RuntimeError("The saved transcript is missing. Refusing to start a replacement conversation.")
                command.extend(["--resume", transcript])
            else:
                command.extend(["--session-id", record["session_id"]])
            command.extend(args)
            self.write(record)
            print(f"Chat: {record['name']} | Account: {account if not shared else 'shared default'} | Mode: {record['mode']}", flush=True)
            result = self.invoke(command, env=env, cwd=project, lease=lease)
        except BaseException:
            if backup:
                private_json(config / ".claude.json", backup[0])
                private_json(config / ".credentials.json", backup[1])
            elif config is not None and previous["mode"] == "shared":
                self.scrub(config, history=False)
            self.write(previous)
            raise
        # A lifecycle event establishes the new launch even if a later request fails.
        self.transcript(record)
        record["last_exit"] = result.returncode
        active = obj(folder / "active.json", True)
        established = active.get("launch_id") == record["launch_id"]
        if result.returncode and not established and (record["mode"], record["profile"]) != (previous["mode"], previous["profile"]):
            previous.update(transcript=record.get("transcript"), session_id=record["session_id"],
                            last_config=record["last_config"], launch_id=record["launch_id"], last_exit=result.returncode)
            if backup:
                private_json(config / ".claude.json", backup[0])
                private_json(config / ".credentials.json", backup[1])
            elif previous["mode"] == "shared":
                self.scrub(folder / "config", history=False)
            self.write(previous)
            print("Claude failed before the chat opened. Previous account choice retained; saved conversation preserved.")
            return result.returncode
        self.write(record)  # Keep recovery metadata even if Credential Manager reconciliation fails.
        if not shared:
            final = snapshot(config)
            if not final or list(identity(final["account"])) != record["identity"]:
                record["state"] = "identity-changed"
                print("Login changed or signed out. An explicit --account is required before resuming.", flush=True)
            elif final != target:
                with operation_lock(self.shared):
                    current = self.vault.get("profile/" + account)
                    if current and digest(current) == digest(baseline):
                        if epoch:
                            final["generation"] = epoch
                        self.vault.put("profile/" + account, final)
        else:
            self.scrub(self.folder(record["name"]) / "config", history=False)
            record["retained_profile"] = None
        self.write(record)
        return result.returncode

    def listing(self):
        rows = []
        for record in self.records():
            try:
                with SessionLease(self.folder(record["name"]) / ".lease"):
                    state = record["state"]
                    if record["mode"] == "pinned" and state != "archived":
                        current = snapshot(self.folder(record["name"]) / "config")
                        if not current or list(identity(current["account"])) != record["identity"]:
                            state = "identity-changed"
            except RuntimeError:
                state = "running"
                if record["mode"] == "pinned":
                    try:
                        current = snapshot(self.folder(record["name"]) / "config")
                        if not current or list(identity(current["account"])) != record["identity"]:
                            state += "; identity-changed"
                    except (ValueError, OSError):
                        state += "; identity-unverified"
            row = dict(record, state=state)
            rows.append(row)
            print(f"{row['name']:<24} {row['mode']:<8} {(row['profile'] or 'shared default'):<16} {state}  {row['project']}")
        if not rows:
            print("No managed chats. Start one with 'claude-account run ACCOUNT --name CHAT'.")
        return rows

    def dependencies(self, account):
        return [r["name"] for r in self.records() if account in (r.get("profile"), r.get("retained_profile"))
                and r.get("state") != "archived"]

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

    def scrub(self, config, history=False):
        if not config.exists():
            return
        reject_links(config)
        for path in config.rglob("*"):
            reject_links(path)
        for path in config.iterdir():
            if path.name == "projects" and not history:
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

    def delete(self, name, history=False, confirm=False):
        record = self.record(name)
        with SessionLease(self.folder(name) / ".lease"):
            if not confirm:
                raise RuntimeError("Deletion requires --yes. Isolated login will be removed; " +
                                   ("managed history will also be removed." if history else "conversation history will be kept."))
            folder = self.folder(name)
            config = folder / "config"
            reject_links(config)
            # Delete only private chat data; imported and shared transcript paths are never removed.
            self.scrub(config, history)
            record.update(state="archived", profile=None, identity=None, retained_profile=None)
            self.write(record)
            print("Deleted managed login for " + name + ". " +
                  ("Its private history was removed; external/shared history is retained." if history else "Conversation history retained."))
