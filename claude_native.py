"""Account-scoped foreground launches using Claude's native conversation store."""
from contextlib import ExitStack
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid

from claude_account import digest, identity, obj
from claude_sessions import Sessions, invoke_claude
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


def publish_history(source, destination):
    """Publish a complete history file atomically without replacing another writer."""
    source, destination = Path(source), Path(destination)
    reject_links(source)
    reject_links(destination)
    private_directory(destination.parent)
    expected = source.read_bytes()
    if destination.exists():
        if destination.read_bytes() != expected:
            raise RuntimeError("Native history conflicts with a legacy file; both copies were retained: " + str(destination))
        return
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".account-migrate-", delete=False) as stream:
        staged = Path(stream.name)
        stream.write(expected)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        if source.read_bytes() != expected:
            raise RuntimeError("Legacy history changed during migration; stop its Claude process first.")
        try:
            staged.rename(destination)  # Windows rename refuses an existing destination.
        except FileExistsError:
            if destination.read_bytes() != expected:
                raise RuntimeError("Another writer created different native history; originals retained.") from None
    finally:
        staged.unlink(missing_ok=True)


class NativeLauncher(Sessions):
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

    def migrate_legacy(self):
        self._initialize()
        with SessionLease(self.root / ".registry"):
            for record in self.records():
                if record.get("native_migrated"):
                    continue
                folder = self.folder(record["name"])
                try:
                    lease = SessionLease(folder / ".lease").__enter__()
                except LeaseBusyError:
                    continue  # Existing old-version processes keep their original files.
                try:
                    self.transcript(record)
                    old_history = folder / "config" / "projects"
                    reject_links(old_history)
                    if old_history.exists():
                        for source in old_history.rglob("*"):
                            reject_links(source)
                            relative = source.relative_to(old_history)
                            if "memory" in relative.parts:
                                continue  # Keep old memories intact; never overwrite current native memory.
                            if source.is_file():
                                publish_history(source, self.history / relative)
                    source = Path(record["transcript"]) if record.get("transcript") else None
                    target = None
                    if source and source.is_file():
                        if source.is_relative_to(old_history):
                            target = self.history / source.relative_to(old_history)
                        elif source.resolve().is_relative_to(self.history.resolve()):
                            target = source
                        else:
                            project_name = re.sub(r"[^a-zA-Z0-9]", "-", record["project"])
                            if len(project_name) > 200:
                                raise RuntimeError("Legacy import has a long project path; retained for explicit transcript resume.")
                            target = self.history / project_name / source.name
                            publish_history(source, target)
                            bundle = source.parent / source.stem
                            reject_links(bundle)
                            if bundle.is_dir():
                                for auxiliary in bundle.rglob("*"):
                                    reject_links(auxiliary)
                                    if auxiliary.is_file():
                                        publish_history(auxiliary, target.parent / source.stem / auxiliary.relative_to(bundle))
                        if not target.is_file():
                            raise RuntimeError("Native transcript migration could not be verified; original retained.")
                    # Recover a fresher same-generation login from a stopped legacy process.
                    retained_owner = record.get("profile") or record.get("retained_profile")
                    if retained_owner:
                        with operation_lock(self.shared):
                            current = self.vault.get("profile/" + retained_owner)
                            final = snapshot_native(folder / "config")
                            if (current and final and current.get("generation") == record.get("epoch")
                                    and identity(current["account"]) == identity(final["account"])
                                    and (final["oauth"].get("expiresAt") or 0) > (current["oauth"].get("expiresAt") or 0)):
                                if current.get("generation"):
                                    final["generation"] = current["generation"]
                                self.vault.put("profile/" + retained_owner, final)
                    # Copy completes before scrub or record transition. Keep original history as a backup.
                    self.scrub(folder / "config", history=False)
                    record.update(native_migrated=True, native_transcript=str(target) if target else None,
                                  legacy_profile=record.get("profile"), legacy_mode=record.get("mode"),
                                  state="archived", profile=None, retained_profile=None, identity=None)
                    self.write(record)
                finally:
                    lease.__exit__()

    def _legacy_args(self, args, force=False):
        args = list(args)
        alias = None
        for index, value in enumerate(args[:-1]):
            if value not in {"--resume", "-r"} or args[index + 1].startswith("-"):
                continue
            name = args[index + 1]
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name):
                continue
            path = self.root / "sessions" / name / "session.json"
            if not path.is_file():
                continue
            record = self.record(name)
            if record.get("native_named") and not force:
                continue
            if not record.get("native_migrated"):
                raise RuntimeError("The legacy chat is still running; exit it before native resume.")
            transcript = record.get("native_transcript")
            if not transcript or not Path(transcript).is_file():
                raise RuntimeError("Legacy conversation history is unavailable; no replacement chat was started.")
            args[index + 1] = transcript
            if not record.get("native_named") and "--fork-session" not in args and "--name" not in args and "-n" not in args:
                args.extend(["--name", name])
            alias = name
            break
        return args, alias

    def _named(self, name, args, result):
        if name and result == 0 and "--fork-session" not in args:
            try:
                with SessionLease(self.root / ".registry"):
                    record = self.record(name)
                    record["native_named"] = True
                    self.write(record)
            except LeaseBusyError:
                pass  # The compatibility name remains usable until a later successful resume.

    def run(self, account, project, args=(), *, legacy=False):
        args = native_args(args)
        project = Path(project).resolve()
        if not project.is_dir():
            raise RuntimeError("The project folder is unavailable.")
        self.check_settings(project, require_hooks=False)
        self.profile(account)
        self.environment()  # Validate before migration or temporary credential writes.
        self.reap()
        self.migrate_legacy()
        args, alias = self._legacy_args(args, force=legacy)
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
                self._named(alias, args, result.returncode)
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

    def resume_alias(self, name, account=None, shared=False, args=()):
        self.reap()
        self.migrate_legacy()
        old = self.root / "sessions" / name / "session.json" if re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name) else None
        record = self.record(name) if old and old.is_file() else None
        project = Path(record["project"]) if record else Path.cwd()
        selected = account or (record.get("legacy_profile") if record and not shared else None)
        forwarded = ["--resume", name, *args]
        if selected:
            return self.run(selected, project, forwarded, legacy=True)
        if shared or (record and record.get("legacy_mode") == "shared"):
            forwarded, alias = self._legacy_args(native_args(forwarded), force=True)
            result = self.invoke([self.cli, *forwarded], env=self.environment(), cwd=project).returncode
            self._named(alias, forwarded, result)
            return result
        raise RuntimeError("Select --account NAME, or use ordinary 'claude --resume' with the shared login.")

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
    from claude_sessions import snapshot
    for name in (".claude.json", ".credentials.json"):
        reject_links(config / name)
    return snapshot(config)
