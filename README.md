# claude-account for Windows

Save Claude Code subscription logins under names, then choose an account for one named chat or change your shared default. `claude-account run work --name billing` starts a chat pinned to `work`. `claude-account use personal` changes the shared default without changing that pin.

This is an independent Windows utility. It works with Claude Code's default **file-backed claude.ai subscription login**. Sessions using a separate configuration folder, an API key, or a cloud provider follow their own authentication settings. Claude Desktop, WSL, and other operating systems are outside the scope of this tool.

## Choose the account scope

| Command | What it changes |
| --- | --- |
| `claude-account use work` | The shared default used by ordinary Claude sessions. |
| `claude-account run work --name billing` | Starts one new chat pinned to `work`; leaves the shared default alone. |
| `claude-account resume billing --account personal` | Changes only the stopped `billing` chat, keeping its conversation. |

`work`, `personal` and `billing` are examples. Substitute your saved account names and a chat name you choose. Exit Claude before resuming or changing a managed chat's account.

## Get started

You need native Windows, Python 3 (tested with 3.13), and a recent `claude.exe` on your PATH. The tool uses Python's standard library and Windows APIs. Start from an ordinary PowerShell window. Sign in with `claude auth login` first if needed.

Clone the repository, or download and extract its ZIP. In the folder containing `claude-account.cmd`, run:

```powershell
.\claude-account.cmd --help
.\claude-account.cmd list
.\claude-account.cmd install
```

The installer copies the Python files into a versioned directory under `%LOCALAPPDATA%\Programs\ClaudeAccountCLI` and places the command in `%USERPROFILE%\.local\bin`. That bin directory must already exist and be on PATH. For portable use, stay in the extracted folder and use `.\claude-account.cmd` in place of `claude-account` below. Installation requires no administrator privileges.

To upgrade an existing installation, get the updated repository files and run `.\claude-account.cmd install` from that folder. The installer replaces the managed launcher with a versioned copy of the new program. Saved accounts, managed chats and the current shared login are retained.

### Save your first accounts

Run these commands one at a time:

```powershell
claude-account save work
claude-account add personal --isolated
claude-account add lab --isolated
claude-account list
```

`save` names the login you already have. Each `add NAME --isolated` opens Claude's browser sign-in and saves the selected account without changing the shared default. Select the intended account, or open the authorization link in its browser profile. Add `--sso` for an explicit SSO flow. If you want the new sign-in to become the shared default immediately, use ordinary `add NAME` without `--isolated`.

You can save additional accounts the same way. Names start with a lowercase letter and contain up to 32 lowercase letters, digits, hyphens, or underscores.

## Pinned chats: the full journey

Start in the project folder where you want Claude to work. Use an already-saved account from `claude-account list`:

```powershell
claude-account run work --name billing
```

The launch banner shows the chat name, selected account and pinned mode. The chat has its own login files. Your shared default stays unchanged, and later shared-default changes do not affect this chat. If you omit `--name`, the command generates a name and displays it. Names use the same rules as account labels.

Exit Claude normally before changing or reopening a managed chat:

```powershell
claude-account sessions
claude-account resume billing
claude-account resume billing --account personal
```

The last command continues the same conversation under `personal` and remembers that choice for subsequent `resume billing` calls. Other managed chats and the shared default keep their choices. It does not migrate in-flight requests or automatically replay a failed prompt. If the new account cannot be selected or Claude fails before opening the chat, the previous selection is retained. Once the new chat has opened, a later API failure or interrupted exit retains the newly selected account.

To follow shared defaults again, or pin the chat again later:

```powershell
claude-account resume billing --shared
claude-account resume billing --account work
```

Shared mode intentionally uses the ordinary shared login. Once the shared-mode process exits, the wrapper scrubs the old isolated login, retaining conversation history. If the wrapper crashes, the retained login remains recorded as a dependency until a successful cleanup.

Extra supported Claude options follow `--`:

```powershell
claude-account run work --name audit -- --model sonnet
claude-account resume audit -- -p "Review the current changes"
```

Model, effort, permission mode, agent and added-directory choices are remembered. Prompts and print-mode options are not replayed on later resumes. Options that would bypass managed login, history or lifecycle tracking are refused, including background launch, custom settings, native resume flags and permission bypass. Normal Claude permission prompts still apply.

### Import an existing conversation

Exit its ordinary Claude process first. Supply the absolute path of its UUID-named transcript file:

```powershell
claude-account run work --name migrated --resume "C:\absolute\path\to\session-uuid.jsonl"
```

Replace the example path with the real transcript. Imported history remains at its original path; Claude may keep appending there. Existing managed conversation claims cannot be imported again under a second name. History within another active managed chat's private configuration remains reserved to that chat. The wrapper cannot detect arbitrary ordinary Claude processes, so do not open the same transcript separately while it is managed.

### Renew or add without changing the default

```powershell
claude-account add client --isolated
claude-account renew work
```

Each command opens Claude's browser login in a temporary private configuration. Select the intended account. Renewal must match the identity already saved under the label; a different account is refused. The shared login stays unchanged. Stop and resume affected pinned chats after renewal so they load the new login. Ordinary `add NAME` retains its original behavior of changing the shared login.

Claude handles normal token refresh inside each running chat. The wrapper reconciles matching credentials at startup and exit, and refuses to overwrite a saved profile changed by another operation. It does not continuously synchronize tokens between running processes. If a provider revokes an older refresh token, stop the affected chat, renew the account, and resume. There is no automatic switch to another account when authentication fails or a limit is reached.

### Inspect, recover and remove

`claude-account sessions` shows each chat's name, pin or shared mode, project and status. A second managed launch or deletion is refused while that named chat is running. The Windows lock remains held by Claude if its wrapper exits unexpectedly; after Claude itself exits, the next managed resume recovers the saved transcript.

If native `/resume` makes two managed names point to the same conversation, later wrapper launches refuse the conflict. Keep the intended name and delete the other with `delete-session NAME --yes` without `--history`. The wrapper does not intercept native `/resume` or ordinary Claude launches while they are running; use managed `resume` commands for lifecycle changes.

Avoid `/login` and `/logout` inside pinned chats. They directly change that private login. The wrapper detects missing or mismatched identity, refuses to save it under the original account name, and requires an explicit `resume CHAT --account NAME` afterward. This is detection and recovery, not interception of Claude's commands. Use the wrapper's account-change commands for a reliable pin.

```powershell
claude-account delete-session billing --yes
claude-account delete-session audit --history --yes
claude-account remove work
```

Deletion scrubs the chat's isolated credentials and archives its managed name. History is kept unless `--history` is supplied. Even with that option, imported and shared transcripts outside the chat's private directory are retained. A retained transcript can be imported under a new name. Removing an account is refused while a nonarchived managed chat still pins or retains its login; repin or delete those chats first.

### Settings and launchers

Pinned chats use current user settings at launch and normal project settings. User instructions, skills, commands, agents, hook files and plugin registry references are seeded when their private configuration is first created. Later edits to those copied files are not automatically synchronized. Managed hooks record only session identity, project and transcript path, so native conversation changes can be recovered. Disabling lifecycle hooks or selecting conflicting authentication settings is refused.

Use `claude-account run` or `resume` from an ordinary terminal, including a manually opened shell pane. Child processes inherit the pinned configuration. Automatic Herdr worker creation, restart and lane resumption have not been integrated or live-tested by this update; launching a plain `claude` process elsewhere still uses that process's own configuration.

## Shared default

```powershell
claude-account use work
claude-account list
```

The asterisk in `list` marks the saved account matching Claude's active email and organization. Run `claude-account` without arguments for a terminal picker. A valid saved login works without another browser sign-in.

For a one-click switch, open the folder where you want the shortcut and run `claude-account shortcut work`. Double-click **Use Claude work.cmd** to select that account. Create a shortcut for each profile you use. A shortcut contains the local interpreter and script paths plus the profile name.

A request already sent uses its original account. New ordinary Claude sessions use the selected shared login; pinned managed chats retain their own account choices. In a local test with simulated subscription credentials and the installed Windows Claude CLI, running workers changed accounts on the next turn while retaining their processes and conversations. Claude Code controls credential caching, and its storage behavior may change between versions.

### Remove a saved profile

```powershell
claude-account remove lab
```

This deletes the named login from Windows Credential Manager if no managed chat still depends on it. If it is the active shared profile, ordinary Claude processes stay signed in. To switch to another saved account and forget the old one, run `claude-account use work` **before** `claude-account remove lab`. Otherwise, the usual unsaved-login check asks you to name the active login before switching away. You can delete any shortcut you made for the removed name.

## If you use Claude's usual login

Using `/login` or `claude auth login` in an ordinary shared session makes the browser-selected account the shared login. Use `claude-account list` to see that shared login. If it is already saved, you can switch away normally. If it is unfamiliar, run `claude-account save NEW-NAME` before switching away. The tool refuses to assign an existing name to a different identity.

Inside a pinned chat, `/login` changes that chat's private login instead. Use `claude-account sessions` to inspect managed chats and follow the managed-chat recovery instructions above.

If a saved login expires or is revoked, use `claude-account renew NAME` and sign back into the same account, then reopen affected managed chats. Use ordinary `claude-account add NAME` only when you also want to change the shared login. If an account switch is interrupted, `list` tells you which `use NAME` command to repeat. If a subsequent external login or refresh prevents recovery, `claude-account recover-login` opens Claude's browser flow to establish the active login.

## Credential handling

- Named profiles contain subscription OAuth credentials and account metadata. They are saved as **Generic Credentials** under `ClaudeAccountCLI/v1/profile/` in Windows Credential Manager for the current Windows user. Treat them as sensitive. Software running as that user may be able to access them.
- Managed chat data lives under `%LOCALAPPDATA%\ClaudeAccountCLI\data\sessions`. Each pinned chat has its own Claude-native `.credentials.json` inside an access-restricted directory. These files are sensitive even though the saved account catalog remains in Credential Manager. Session metadata and hooks do not store tokens or prompt content. Chat transcripts remain sensitive.
- Switching updates the account-specific portions of Claude's native login files while retaining unrelated settings and MCP credentials. The tool uses local locks and records an interrupted switch in Credential Manager for recovery. Claude performs browser authentication and token renewal.
- For support, share the command name and a redacted error. Keep credentials, login codes, transcripts, and account files private. `list` output includes email and organization details. Generated shortcuts contain machine-specific paths.
- To sign the active session out, use Claude's own logout command. To uninstall the CLI, exit managed chats and run `claude-account delete-session NAME --yes` for each one to scrub its isolated login while retaining history. Then remove saved profiles with `claude-account remove NAME` and delete the installed `claude-account.cmd`, its `ClaudeAccountCLI` program folder, and any shortcuts you created. Retained transcripts remain in their original locations.

See [Claude Code authentication](https://code.claude.com/docs/en/authentication) for account types and login restrictions.

## Run the tests

On Windows, from the repository root:

```powershell
python -B -m unittest discover -s tests -v
```

The default suite uses invented account records, disposable files and an in-memory substitute for Credential Manager. It exercises pinning, switching, crash recovery, renewal races, deletion, real Windows lock inheritance and installation into a disposable home.

The optional integration test runs the installed native Claude executable with invented credentials against a loopback API that returns simulated responses. It observes the credential selected for each request, conversation continuity, a default change between turns of one live process, lifecycle hooks and a simulated account limit. It does not sign into a real account or validate live browser authentication, provider token renewal, or live Herdr integration.

```powershell
$env:CLAUDE_ACCOUNT_NATIVE_TEST = '1'
python -B -m unittest discover -s tests -v
Remove-Item Env:CLAUDE_ACCOUNT_NATIVE_TEST
```

Run the optional test only where local loopback connections and the installed Claude executable are permitted. Native Claude storage and hook interfaces can change; re-run it after a Claude upgrade before relying on the same compatibility assumptions.
