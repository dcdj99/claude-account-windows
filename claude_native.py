"""Account-scoped foreground launches using Claude's native conversation store."""
from contextlib import ExitStack
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

from claude_account import digest, identity, obj
from claude_auth import AccountRuntime, invoke_claude, snapshot
from claude_account_windows import (LeaseBusyError, SessionLease, create_history_junction, operation_lock,
                                    private_directory, private_json, reject_links,
                                    remove_history_junction)

VALUE_OPTIONS = {"--model", "--effort", "--permission-mode", "--agent", "--add-dir", "--name", "-n",
                 "--output-format", "--input-format", "--max-turns", "--max-budget-usd"}
SWITCH_OPTIONS = {"--continue", "-c", "--fork-session", "--print", "-p", "--verbose", "--debug"}


def native_args(args):
    """Forward native conversation options; reject auth, detach and settings escapes."""
    result, index, prompts = [], 0, 0
    args = list(args)
    while index < len(args):
        value = args[index]
        if "=" in value and value.startswith("--"):
            key, data = value.split("=", 1)
            args[index:index + 1] = [key, data]
            value = key
        if value in VALUE_OPTIONS:
            if index + 1 >= len(args) or args[index + 1].startswith("-"):
                raise RuntimeError("Missing value for " + value)
            if value == "--permission-mode" and args[index + 1] == "bypassPermissions":
                raise RuntimeError("Account launches do not enable permission bypass.")
            result.extend(args[index:index + 2])
            index += 2
        elif value in {"--resume", "-r"}:
            result.append(value)
            index += 1
            if index < len(args) and not args[index].startswith("-"):
                result.append(args[index])
                index += 1
        elif value in SWITCH_OPTIONS:
            result.append(value)
            index += 1
        elif value.startswith("-"):
            raise RuntimeError("Unsupported account-launch option: " + value)
        else:
            prompts += 1
            if prompts > 1 or value in {"auth", "setup-token", "update", "install", "mcp", "agents", "attach", "purge"}:
                raise RuntimeError("Pass one prompt, not a Claude subcommand.")
            result.append(value)
            index += 1
    if any(v in result for v in ("--continue", "-c")) and any(v in result for v in ("--resume", "-r")):
        raise RuntimeError("Choose native --resume or --continue, not both.")
    return result


class NativeLauncher(AccountRuntime):
    def __init__(self, home, root, vault, cli, invoke=invoke_claude):
        super().__init__(home, root, vault, cli, invoke)
        self.history = self.shared / "projects"
        self.launches = self.root / "launches"

    def _initialize(self):
        private_directory(self.root)
        private_directory(self.launches)
        private_directory(self.shared)
        private_directory(self.history)

    def _reconcile(self, folder, record):
        config = folder / "config"
        reject_links(config)
        final = snapshot_native(config)
        if not final or list(identity(final["account"])) != record.get("identity"):
            return
        with operation_lock(self.shared):
            current = self.vault.get("profile/" + record["profile"])
            if current and digest(current) == record.get("baseline"):
                if record.get("generation"):
                    final["generation"] = record["generation"]
                self.vault.put("profile/" + record["profile"], final)

    def _scrub_launch(self, folder):
        """Scrub private files even if the history link is damaged; never follow it."""
        reject_links(folder)
        config = folder / "config"
        reject_links(config)
        if not config.exists():
            return
        errors = []
        link = config / "projects"
        try:
            if os.path.lexists(link):
                if link.is_junction():
                    remove_history_junction(link, self.history)
                elif link.is_dir() and not any(link.iterdir()):
                    reject_links(link)
                    link.rmdir()  # A failed setup can leave an empty ordinary directory.
                else:
                    raise RuntimeError("Unexpected native history entry; retained for inspection.")
        except (OSError, RuntimeError) as error:
            errors.append(error)
        for child in config.iterdir():
            if child.name == "projects":
                continue
            try:
                reject_links(child)
                if child.is_dir():
                    for descendant in child.rglob("*"):
                        reject_links(descendant)
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except (OSError, RuntimeError) as error:
                errors.append(error)
        if errors:
            raise RuntimeError("Private launch cleanup needs attention; native history was not traversed: " + str(folder))
        config.rmdir()

    def reap(self):
        self._initialize()
        with SessionLease(self.root / ".registry"):
            for folder in self.launches.iterdir():
                if not re.fullmatch(r"[0-9a-f]{32}", folder.name):
                    continue
                reject_links(folder)
                try:
                    lease = SessionLease(folder / ".lease").__enter__()
                except LeaseBusyError:
                    continue
                failure = None
                try:
                    try:
                        record = obj(folder / "native.json", True)
                        if record:
                            self._reconcile(folder, record)
                    except (ValueError, KeyError, TypeError) as error:
                        failure = error
                    finally:
                        self._scrub_launch(folder)
                finally:
                    lease.__exit__()
                shutil.rmtree(folder)
                if failure is not None:
                    raise RuntimeError("Removed a stopped launch with damaged metadata; its private login was scrubbed. Retry the command.") from failure


    def run(self, account, project, args=()):
        args = native_args(args)
        project = Path(project).resolve()
        if not project.is_dir():
            raise RuntimeError("The project folder is unavailable.")
        self.check_settings(project)
        self.profile(account)
        self.environment()  # Validate before temporary credential writes.
        self.reap()
        folder = self.launches / uuid.uuid4().hex
        config = folder / "config"
        with ExitStack() as handles:
            with SessionLease(self.root / ".registry"):
                private_directory(folder)
                lease = handles.enter_context(SessionLease(folder / ".lease"))
            try:
                with operation_lock(self.shared):
                    baseline = self.profile(account)
                    target = self._latest(baseline, epoch=baseline.get("generation"))
                    self.seed(config, target)
                    env = self.verify(config, target, project)
                    record = dict(profile=account, project=str(project), identity=list(identity(target["account"])),
                                  baseline=digest(baseline), generation=baseline.get("generation"), started=time.time())
                    private_json(folder / "native.json", record)
                create_history_junction(config / "projects", self.history)
                settings = folder / "launch-settings.json"
                private_json(settings, obj(self.shared / "settings.json", True))
                env.update(CLAUDE_ACCOUNT_PROFILE=account, CLAUDE_ACCOUNT_MODE="native")
                print("Account: " + account + " | Native Claude conversation | Shared default unchanged", flush=True)
                # A resumed cached prompt must not retain paths into a deleted launch directory.
                result = self.invoke([self.cli, "--settings", str(settings), "--system-prompt-snapshot", "off", *args],
                                     env=env, cwd=project, lease=lease)
                self._reconcile(folder, record)
                return result.returncode
            finally:
                try:
                    self._scrub_launch(folder)
                finally:
                    try:
                        with SessionLease(self.root / ".registry"):
                            handles.close()
                            # Keep damaged history for inspection, never recursively follow it.
                            if not config.exists() and folder.exists():
                                shutil.rmtree(folder)
                    except LeaseBusyError:
                        # Credentials are already scrubbed. A later invocation removes the empty record.
                        pass
                    finally:
                        handles.close()


    def active(self, display=False):
        self._initialize()
        with SessionLease(self.root / ".registry"):
            return self._active_locked(display)

    def _active_locked(self, display):
        rows = []
        if not self.launches.exists():
            return rows
        reject_links(self.launches)
        for folder in self.launches.iterdir():
            if not re.fullmatch(r"[0-9a-f]{32}", folder.name):
                continue
            reject_links(folder)
            record = obj(folder / "native.json", True)
            if not record:
                continue
            try:
                with SessionLease(folder / ".lease"):
                    continue
            except LeaseBusyError:
                rows.append(record)
                if display:
                    print(record["profile"] + "  running  " + record["project"])
        if display and not rows:
            print("No account-launched Claude processes are running. Use 'claude --resume' for native history.")
        return rows


def snapshot_native(config):
    for name in (".claude.json", ".credentials.json"):
        reject_links(config / name)
    return snapshot(config)
