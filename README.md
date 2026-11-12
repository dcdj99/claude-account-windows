# claude-account for Windows

Choose a saved Claude Code subscription account for one launch, while Claude manages conversations and history.

The easiest way to resume is:

```powershell
claude-account resume
```

Choose a saved account, then choose a conversation in **Claude's native picker**. Your shared default stays unchanged. Running `claude-account` without arguments opens a menu for starting, resuming, continuing, inspecting accounts, or explicitly changing the shared default.

## Short commands

Run from your project folder. `work` and `personal` are example saved account names.

| Command | Result |
| --- | --- |
| `claude-account start` | Choose an account and start a conversation. |
| `claude-account start work` | Start with `work`. |
| `claude-account start work billing` | Start with `work` and name it `billing`. |
| `claude-account resume` | Choose an account, then use Claude's conversation picker. |
| `claude-account resume personal` | Open Claude's picker using `personal`. |
| `claude-account resume personal billing` | Resume `billing` using `personal`. |
| `claude-account continue work` | Continue the latest conversation using `work`. |
| `claude-account fork work billing experiment` | Branch `billing` into `experiment` using `work`. |
| `claude-account sessions` | Show currently running account launches. |

Quote names containing spaces. The conversation argument also accepts a native session ID or absolute `.jsonl` transcript path. Account labels use lowercase letters, digits, hyphens and underscores, start with a letter, and are at most 32 characters.

To change the account for a conversation, exit Claude and resume it with the other saved account:

```powershell
claude-account resume personal billing
```

The choice applies to this launch. It is not remembered as a permanent pin. Other launches and the shared default keep their choices. Do not open the same conversation in multiple foreground processes; the wrapper cannot prevent arbitrary native writers from interleaving history. There is no automatic account fallback or prompt replay.

The wrapper does not build a historical session list. Native naming, `/rename`, resume, continue, forks, project filtering and retention remain Claude's responsibility. Print-mode conversations follow Claude's own picker/continue rules. `sessions` contains only running launches. Native history uses the ordinary `~/.claude/projects` location, so plain `claude --resume` can see it and uses the shared login.

## Save and manage accounts

Use an ordinary PowerShell window. Sign in with `claude auth login` first if needed.

```powershell
claude-account save work
claude-account add personal --isolated
claude-account list
claude-account renew work
```

`save` names the current shared login. `add NAME --isolated` opens Claude's browser login and saves the chosen account without changing the shared default. Select the intended browser account; add `--sso` for enterprise SSO. Ordinary `add NAME` without `--isolated` also changes the shared login.

Renewal requires the same saved identity and leaves the shared default unchanged. Exit and reopen affected launches to load the renewed login. Claude handles refresh inside a running launch. Matching refreshed credentials are saved at exit or stopped-launch recovery only if the saved account has not changed since launch. Tokens are not continuously synchronized between running processes.

To intentionally change the shared default, use `claude-account use work` or choose **Change the shared default account** in the menu. Selecting an account for start/resume/continue does not change it. `list` marks the current shared account, and `shortcut work` creates a one-click shared-default switch in the current folder.

`claude-account remove NAME` forgets a saved label once no running launch uses it. Native conversations are retained. Removing the active shared profile does not sign ordinary Claude out; use Claude's own logout command for that.

## Install or upgrade

This independent utility supports native Windows and Claude Code's file-backed **claude.ai subscription login**. It requires Python 3.12 or later and a recent `claude.exe` on PATH. API keys, alternate Credential Manager login backends, custom configuration directories, cloud-provider authentication, WSL and Claude Desktop are outside its scope.

Download and extract the package, or clone the repository. In the folder containing the command:

```powershell
.\claude-account.cmd --help
.\claude-account.cmd install
```

The installer places a versioned runtime under `%LOCALAPPDATA%\Programs\ClaudeAccountCLI` and the command under `%USERPROFILE%\.local\bin`. That bin directory must already exist and be on PATH. Administrator privileges are not required. For portable use, call the extracted command by its full path. Installation preserves saved accounts and the shared login.

## Legacy removal

The old per-chat registry, migration engine, lifecycle-recording hook and compatibility commands have been removed. `migrate-sessions`, `delete-session`, `sessions --legacy` and `resume CHAT --account ACCOUNT` are no longer supported. Use `resume ACCOUNT [CONVERSATION]` instead. Old top-level `run --name/--resume` aliases are removed; native flags still work after `run ACCOUNT --`.

This version does not inspect, modify or delete old private chat files. If upgrading from a version with unmigrated private history, migrate it with the prior version first, or resume its transcript explicitly with the new command. Already native conversations need no migration. No legacy tracking files are created or updated by the new runtime.

## Options and settings

Extra Claude options follow `--`: model, effort, permission mode, agent, added directories, print, input/output formats, turn/budget limits, verbose and debug. The short commands supply the name/resume/continue/fork options for you. For complete native option control, use `claude-account run ACCOUNT -- NATIVE-OPTIONS`.

```powershell
claude-account start work audit -- --model sonnet
claude-account resume personal audit -- -p "Review the changes"
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
- The Windows lease stays held by Claude if its wrapper dies. After Claude exits, the next launch, `sessions` or removal command cleans stopped launch files. Cleanup removes only the history link, never traverses it recursively.
- Shared `use` preserves unrelated login fields and MCP credentials, with interrupted-switch recovery. If an external login prevents recovery, `claude-account recover-login` opens Claude's browser flow. Save an unfamiliar shared login under a new label before switching away.

For support, share redacted errors and command names, not credentials, login codes or private transcripts. `list` contains identifying account details.

To uninstall, exit account launches and run `claude-account sessions` to clean stopped files. Remove saved labels if desired, then delete the installed command and its program folder. Native history is retained.

## Tests

From the source folder on Windows:

```powershell
python -B -m unittest discover -s tests -v
```

Tests use invented accounts, disposable files and an in-memory credential catalog. They cover menu cancellation, account selection, short-command routing, rejection of removed legacy commands, credential isolation, renewal races, cleanup, real Windows junctions and inherited leases, and installation.

For the real Claude executable against a loopback API with invented credentials:

```powershell
$env:CLAUDE_ACCOUNT_NATIVE_TEST = '1'
python -B -m unittest discover -s tests -v
Remove-Item Env:CLAUDE_ACCOUNT_NATIVE_TEST
```

The integration test routes short commands through the real CLI and observes the selected request credentials, native name/ID/path resume, continue, forks, shared resume, account stability between turns and no fallback after a simulated API error. Real browser authentication, provider token refresh, billing and live Herdr integration are not exercised. Re-run after a Claude upgrade.

The optional manual native-picker scenario is selected with `CLAUDE_ACCOUNT_PICKER_TEST=1` as well as the integration flag. It uses a disposable project and simulated API; Claude may ask you to trust that test folder. Follow the test's printed instructions.
