# claude-account for Windows

Save Claude Code subscription logins, then choose an account for a single launch or change your shared default. Claude itself owns conversation names, history, resume, continue and forks.

```powershell
claude-account run work -- --name billing
# Exit Claude, then resume the same conversation with another saved account:
claude-account run personal -- --resume billing
```

Each launch has a private login. Later `claude-account use lab` changes do not affect it. The account choice lasts for that launch; it is not permanently attached to the conversation.

This independent utility supports native Windows and Claude Code's file-backed **claude.ai subscription login**. It requires Python 3.12 or later and a recent `claude.exe` on PATH. The test report records tested versions. API keys, alternate Credential Manager login backends, separate configuration folders, cloud-provider authentication, WSL and Claude Desktop are outside its scope.

## Install or upgrade

Download and extract the package, or clone the repository. From the folder containing `claude-account.cmd`:

```powershell
.\claude-account.cmd --help
.\claude-account.cmd install
```

The installer copies the runtime into a versioned directory under `%LOCALAPPDATA%\Programs\ClaudeAccountCLI` and places the command in `%USERPROFILE%\.local\bin`. That bin directory must already exist and be on PATH. Administrator privileges are not required. For portable use, use the full path to the extracted command instead.

The same command upgrades an existing managed installation, retaining saved accounts and the shared login. Old managed chats are migrated when you use the new launcher, as described below.

## Save accounts

Use an ordinary PowerShell window. If needed, first sign in with `claude auth login`.

```powershell
claude-account save work
claude-account add personal --isolated
claude-account list
```

`save` names the current login. `add NAME --isolated` opens Claude's browser sign-in and saves the selected account without changing the shared default. Select the intended browser account; add `--sso` for enterprise SSO. Ordinary `add NAME` without `--isolated` also changes the shared login.

Account labels start with a lowercase letter and contain up to 32 lowercase letters, digits, hyphens or underscores. Conversation names are native Claude names and may contain spaces.

## The conversation journey

Run these commands from your project folder. `work`, `personal` and `lab` are example saved accounts.

### Start and name a conversation

```powershell
claude-account run work -- --name billing
```

The banner shows the chosen account. Work normally in Claude; native `/rename` can change the conversation's name. Omitting `--name` lets Claude manage the title. The wrapper does not generate a permanent tracking name. Normal Claude permissions still apply.

### Close and resume with another account

Exit the old Claude process first:

```powershell
claude-account run personal -- --resume billing
```

This continues the same conversation under `personal` without changing the shared default. Choose the account explicitly each time you launch.

Claude's native picker, latest conversation, session ID and absolute transcript path are also supported:

```powershell
claude-account run personal -- --resume
claude-account run personal -- --continue
claude-account run personal -- --resume SESSION-UUID
claude-account run personal -- --resume "C:\absolute\path\to\SESSION-UUID.jsonl"
```

Replace the UUID/path examples with real values. Native project filtering applies. Print-mode conversations follow Claude's own picker/continue rules, which differ from interactive conversations. See [Claude's session documentation](https://code.claude.com/docs/en/sessions).

To branch the conversation:

```powershell
claude-account run work -- --resume billing --fork-session --name billing-experiment
```

Exit the old process before changing its account. The wrapper cannot prevent arbitrary native processes from writing the same transcript concurrently. There is no automatic account fallback or replay of a failed prompt.

### Return to ordinary Claude

```powershell
claude --resume billing
```

The conversation is in the ordinary native history store. Plain Claude uses the shared login, not the account chosen for a previous wrapper launch.

To change that shared default intentionally:

```powershell
claude-account use lab
claude-account list
```

The asterisk marks the saved account matching the current shared email and organization. A valid saved login normally requires no browser sign-in. Run `claude-account` without arguments for the shared-account picker. `claude-account shortcut work` creates a one-click shared-account switch in the current folder.

### Inspect running processes and saved history

```powershell
claude-account sessions
claude-account run work -- --resume
```

`sessions` shows only currently running account-launched processes, with account and project. Stopped launches do not accumulate there. Claude's resume picker shows conversation history. Native retention and deletion remain Claude's responsibility, including `cleanupPeriodDays` and native purge functionality. The launcher does not override retention.

### Renew or remove a saved account

```powershell
claude-account renew work
claude-account remove lab
```

Renewal uses isolated browser login and requires the identity already saved under the label. Exit and reopen affected processes to load the renewed login. The shared default stays unchanged.

Claude handles token refresh inside a launch. On exit or stopped-launch recovery, the wrapper saves matching refreshed credentials only if the saved account has not changed since launch. Tokens are not continuously synchronized across processes. If a provider invalidates an older token, exit, renew and resume.

Removal is refused while an account launch is running or an unmigrated old chat retains the login. Native conversations survive removal of a saved label. Removing the active shared profile does not sign ordinary Claude out; use Claude's logout command for that.

## Upgrade old pinned chats

New launches do not create the old per-chat registry. Existing records remain as a finite compatibility archive.

Exit old managed Claude processes, then optionally migrate explicitly:

```powershell
claude-account sessions --legacy
claude-account migrate-sessions
claude-account run personal -- --resume billing
```

Normal launches also run migration. Running old chats are skipped. Stopped legacy transcripts and associated files are copied into native history without overwriting different files. Conflicts stop migration and retain originals. Private login files are scrubbed only after the history copy succeeds. Original private history remains as a backup; old memory directories stay in that backup rather than being merged over current native memory.

The first successful resume through an old label applies a native name. Later `run ... -- --resume NAME` follows native naming, including subsequent `/rename` changes. The old shortcut can still resolve the original legacy label directly:

```powershell
claude-account resume billing --account personal
```

For a legacy record, `resume billing` without an account uses its original saved account as a compatibility fallback. New native conversations require `--account NAME` or `--shared` with this shortcut. The shortcut does not remember a new permanent pin. Prefer explicit `run ACCOUNT -- --resume NAME`.

Old top-level `run ACCOUNT --name NAME` and `run ACCOUNT --resume PATH` flags remain aliases for native flags. `delete-session NAME --yes` operates only on old records. Its `--history` option removes the old private backup, not migrated native history or external imported transcripts. It is not the native conversation deletion command.

## Options and settings

Supported Claude options follow `--`: name, resume, continue, fork, model, effort, permission mode, agent, added directories, print, input/output formats, turn/budget limits, verbose and debug.

```powershell
claude-account run work -- --name audit --model sonnet
claude-account run personal -- --resume audit -p "Review the changes"
```

The wrapper stores no per-conversation model or option preferences. Claude restores native conversation state according to its own rules.

Custom settings/config overrides, alternative authentication, background dispatch and permission-bypass flags are refused. Every account launch supplies a settings file, which also makes current Claude versions refuse automatic attachment to a running background session. Stop that session first or fork it.

The wrapper does not intercept in-session `/resume`, `/login`, `/logout`, background controls or child programs. These can change native behavior. Exit and launch explicitly for a reliable account change. A different login identity is never saved over the originally selected account label.

Current user settings, instructions, skills, commands, agents, hook files and plugin registry references are copied into the private launch configuration. Project settings apply normally. Custom lifecycle hooks are not required; disabling hooks is supported. Conflicting authentication settings are refused. Enterprise managed settings and native permissions still apply.

User-level configuration edits made inside the temporary configuration are discarded at exit. Make persistent global changes in your ordinary Claude configuration. Project edits and native conversation history persist. Launches use Claude's documented `--system-prompt-snapshot off` option so resumed context does not retain paths to a deleted launch directory.

Automatic Herdr worker creation/restart is not integrated. A plain `claude` launched elsewhere uses that process's own configuration.

## Storage and recovery

- Saved credentials remain in Windows Credential Manager under `ClaudeAccountCLI/v1/profile/` for the current user.
- Protected temporary logins live under `%LOCALAPPDATA%\ClaudeAccountCLI\data\launches`. A validated directory junction shares only `projects` with `%USERPROFILE%\.claude\projects`. Credential paths remain private and reject links.
- Native transcripts and project memory are shared across chosen accounts and plain Claude. This provides one native history, not separate account-specific histories. Credentials and transcripts remain sensitive local data.
- The Windows lease stays held by Claude if its wrapper dies. After Claude exits, the next run, `sessions`, migration or removal command cleans stopped launch files. Cleanup removes only the history link, never traverses it recursively.
- Shared `use` preserves unrelated login fields and MCP credentials, with interrupted-switch recovery. If an external login prevents recovery, `claude-account recover-login` opens Claude's browser flow. Save an unfamiliar shared login under a new label before switching away.

For support, share redacted errors and command names, not credentials, login codes or private transcripts. `list` contains identifying account details.

To uninstall, exit account launches and run `claude-account sessions` to clean stopped files. Migrate remaining old chats first. Remove saved labels if desired, then delete the installed command and its program folder. Native history is retained.

## Tests

From the source folder on Windows:

```powershell
python -B -m unittest discover -s tests -v
```

Default tests use invented profiles, an in-memory credential catalog and disposable files. They cover isolation, refresh/renewal races, migration conflicts and backups, cleanup, real Windows junctions and inherited leases, installation and legacy behavior.

Optional integration tests use the installed Claude executable, invented credentials and a loopback API with simulated responses:

```powershell
$env:CLAUDE_ACCOUNT_NATIVE_TEST = '1'
python -B -m unittest discover -s tests -v
Remove-Item Env:CLAUDE_ACCOUNT_NATIVE_TEST
```

They check actual credentials selected for requests, native name/ID/path resume, continue, forks, ordinary shared resume, account stability between turns, migration and no fallback after an API error. Real browser authentication, provider token refresh, billing and Herdr integration are not exercised. Re-run after Claude upgrades.

An optional manual terminal test exercises the actual picker with the same isolated API. Follow its printed instructions; Claude may request trust for the disposable folder:

```powershell
$env:CLAUDE_ACCOUNT_NATIVE_TEST = '1'
$env:CLAUDE_ACCOUNT_PICKER_TEST = '1'
python -B -m unittest discover -s tests -p test_native_flow.py -v
Remove-Item Env:CLAUDE_ACCOUNT_PICKER_TEST
Remove-Item Env:CLAUDE_ACCOUNT_NATIVE_TEST
```
