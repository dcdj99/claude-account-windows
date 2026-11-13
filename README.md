# claude-account for Windows

A command-line tool for using multiple Claude Code subscription accounts on Windows. Save your logins under names such as `work` and `personal`, then choose which account to use when starting or resuming a conversation.

For example, you can start a conversation with your work account and resume it later with your personal account, while keeping the login used by the ordinary `claude` command unchanged.

## Before you start

You need:

- Native Windows and PowerShell.
- [Python](https://www.python.org/downloads/windows/) 3.12 or later, available as `python`.
- The native [Claude Code CLI](https://code.claude.com/docs/en/setup#install-on-native-windows), available as `claude.exe`.
- A Claude subscription account for each login you want to save.

This tool supports Claude Code's standard file-backed Claude subscription login. API keys, cloud-provider authentication, alternate credential backends, WSL and Claude Desktop are outside its scope. It uses Python's standard library; no additional Python packages are required.

## Install

1. [Download the source ZIP](https://github.com/dcdj99/claude-account-windows/archive/refs/heads/main.zip) and extract it, or clone this repository.
2. Open PowerShell in the extracted folder containing `claude-account.cmd`.
3. Check the requirements and install the command:

   ```powershell
   python --version
   claude.exe --version
   .\claude-account.cmd install
   ```

The installer makes `claude-account` available through `%USERPROFILE%\.local\bin`. That folder must already exist and be on your PATH. If installation reports a PATH problem, follow the instructions under [Troubleshooting](#troubleshooting).

Verify the installation:

```powershell
claude-account --help
```

Administrator privileges are not required. The program is installed under `%LOCALAPPDATA%\Programs\ClaudeAccountCLI`.

## Save your accounts

First, sign in to the account you want to call `work`. Skip the login command if Claude is already signed in to that account:

```powershell
claude auth login
claude-account save work
```

`save work` gives your current login a name; it does not create a new Claude account.

Add another account:

```powershell
claude-account add personal --isolated
```

Claude opens its browser sign-in flow. Select your personal account. The `--isolated` option keeps your current shared login unchanged. For an enterprise SSO sign-in, add `--sso`.

Check your saved accounts:

```powershell
claude-account list
```

`work` and `personal` are example labels. Choose your own lowercase labels, starting with a letter and using letters, digits, hyphens or underscores, up to 32 characters.

## Start or resume a conversation

Open PowerShell in the project folder where you want Claude to work, then run:

```powershell
claude-account
```

The menu lets you start, resume or continue a conversation, choose an account, and view running launches. Press Enter at a menu prompt to cancel.

To go straight to resuming:

```powershell
claude-account resume
```

Choose a saved account, then choose a conversation in Claude's native picker. You do not need to remember a conversation name or session ID.

### Shortcuts

| What you want to do | Command |
| --- | --- |
| Start using a saved account | `claude-account start work` |
| Start and name a conversation | `claude-account start work billing` |
| Pick a conversation using a particular account | `claude-account resume personal` |
| Resume a named conversation | `claude-account resume personal billing` |
| Continue the latest conversation in this project | `claude-account continue work` |
| Make a separate branch of a conversation | `claude-account fork work billing experiment` |
| See currently running account launches | `claude-account sessions` |

Quote conversation names containing spaces, for example `claude-account start work "Billing report"`. You can also use Claude's `/rename` command inside a conversation. A resume argument can be a native session ID or an absolute `.jsonl` transcript path.

### Resume with a different account

Start a conversation:

```powershell
claude-account start work billing
```

When you want to use another account, **exit that Claude process first**, then resume from the same project folder:

```powershell
claude-account resume personal billing
```

The conversation continues using `personal`. The selected account applies to that launch; choose an account again when reopening the conversation. Other account-launched processes keep their own logins.

Claude manages conversation history, names, project filtering and retention. Accounts share the native local history on this Windows user profile. You can also resume these conversations with ordinary `claude --resume`, which uses the shared login.

Avoid opening the same conversation in multiple processes at once. There is no automatic switch to another account or replay of your prompt if a request fails.

## Choose the shared default account

The **shared default** is the login used when you launch ordinary Claude Code with `claude`.

Starting or resuming through `claude-account` leaves that default unchanged. To change it intentionally:

```powershell
claude-account use personal
```

You can also choose **Change the shared default account** from the menu. The asterisk in `claude-account list` marks the account matching the current shared login.

## Manage saved accounts

| Task | Command |
| --- | --- |
| List accounts and the shared login | `claude-account list` |
| Add an account without changing the shared login | `claude-account add client --isolated` |
| Sign in again to renew a saved login | `claude-account renew work` |
| Forget a saved account label | `claude-account remove work` |
| Create a shared-account switching shortcut in the current folder | `claude-account shortcut work` |

Renewal opens a browser and requires the identity already saved under that label. Exit and reopen affected launches afterward. Ordinary `add NAME` without `--isolated` also changes the shared login.

Removing a label keeps conversation history. It is refused while an account launch using that label is running. Removing the current shared profile does not sign ordinary Claude out; use Claude's own logout command to sign out.

## Troubleshooting

**The installer says `.local\bin` is missing or is not on PATH.** Create `%USERPROFILE%\.local\bin`, add that folder to your Windows user PATH, reopen PowerShell and run the installer again. PATH is the Windows setting that lets commands run from any folder. For portable use, skip installation and call the extracted script by its full path, for example:

```powershell
& "C:\Tools\claude-account\claude-account.cmd" resume
```

Replace the example path with the folder where you extracted the files.

**A saved login has expired or stopped working.** Run `claude-account renew NAME`, sign in to the same account, then reopen the conversation.

**Claude reports an unsupported authentication setting.** Use an ordinary PowerShell window with the standard Claude subscription login. API-key or provider overrides and custom authentication settings cannot be combined with these account launches.

**Your current shared login is not saved.** Run `claude-account save NAME` before using `use` to switch away. If a shared-account switch was interrupted, `list` gives recovery instructions. Use `recover-login` if an external login prevents that recovery.

**A conversation is missing from the picker.** Check that you are in the correct project folder. Claude applies its own picker rules, including different handling for print-mode conversations. If you know the conversation's name, ID or transcript path, pass it to `resume` after the account name.

**You want to change accounts inside a conversation.** Exit and use `claude-account resume` to select the next account. Native `/login`, `/logout` and background-session controls are not managed by this tool.

## Advanced options and settings

Extra Claude options go after `--`:

```powershell
claude-account start work audit -- --model sonnet
claude-account resume personal audit -- -p "Review the changes"
```

Supported extras include model, effort, permission mode, agent, added directories, print, input/output formats, turn/budget limits, verbose and debug. For native name/resume/continue/fork flags, use the advanced `run` command:

```powershell
claude-account run work -- --resume audit --model sonnet
```

Custom configuration overrides, alternative authentication, background dispatch and permission-bypass flags are refused. Native Claude permissions still apply. Stop a running background session before resuming it with another account, or fork the conversation.

Launches use a temporary private login configuration with copies of current user settings and extensions. Project settings apply normally. User-level settings edited inside a temporary launch are discarded when it closes; make lasting global changes in your ordinary Claude configuration. Project changes and native conversation history persist.

## Privacy and storage

Saved account credentials are stored in Windows Credential Manager for the current Windows user. Running launches use protected temporary login files that are cleaned up after exit or recovered on a later launch after a crash. Software running as the same Windows user may be able to access these credentials.

Conversations stay in Claude's native history under `%USERPROFILE%\.claude\projects`. The `sessions` command shows running launches, not an accumulating conversation list. Treat transcripts as sensitive, and do not share credentials, login codes or account files when reporting a problem. `list` output includes identifying account information.

## Update or uninstall

To update, download the latest source and run `.\claude-account.cmd install` from that folder. Saved accounts, the shared login and native conversation history are retained.

To uninstall, exit account launches and run `claude-account sessions` to clean stopped launch files. Remove saved labels if desired, then delete `%USERPROFILE%\.local\bin\claude-account.cmd` and `%LOCALAPPDATA%\Programs\ClaudeAccountCLI`. Native conversation history is retained.

## Development

Run the tests from the repository folder on Windows:

```powershell
python -B -m unittest discover -s tests -v
```

The tests use invented accounts and disposable files. To include the installed Claude executable against a simulated local API:

```powershell
$env:CLAUDE_ACCOUNT_NATIVE_TEST = '1'
python -B -m unittest discover -s tests -v
Remove-Item Env:CLAUDE_ACCOUNT_NATIVE_TEST
```

These tests do not sign in to real accounts or exercise live provider billing or token revocation.
