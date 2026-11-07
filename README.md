# claude-account for Windows

Save work, personal, or other Claude Code subscription logins under names you choose. Run `claude-account use work` to make one the shared default for your Windows user. Claude Code workers using that login can pick it up on their next turn, including workers already running in Herdr. The switch runs locally and requires no model prompt or Herdr restart.

This is an independent Windows utility. It works with Claude Code's default **file-backed claude.ai subscription login**. Sessions using a separate configuration folder, an API key, or a cloud provider follow their own authentication settings. Claude Desktop, WSL, and other operating systems are outside the scope of this tool.

## Get started

You need native Windows, Python 3 (tested with 3.13), and a recent `claude.exe` on your PATH. The tool uses Python's standard library and Windows APIs. Start from an ordinary PowerShell window. Sign in with `claude auth login` first if needed.

Clone the repository, or download and extract its ZIP. In the folder containing `claude-account.cmd`, run:

```powershell
.\claude-account.cmd --help
.\claude-account.cmd list
.\claude-account.cmd install
```

The installer copies the Python files into a versioned directory under `%LOCALAPPDATA%\Programs\ClaudeAccountCLI` and places the command in `%USERPROFILE%\.local\bin`. That bin directory must already exist and be on PATH. For portable use, stay in the extracted folder and use `.\claude-account.cmd` in place of `claude-account` below. Installation requires no administrator privileges.

### Save your first accounts

Run these commands one at a time:

```powershell
claude-account save work
claude-account add personal
claude-account add lab
claude-account list
```

`save` names the login you already have. Each `add` opens Claude's normal browser sign-in. Select the intended account, or open the authorization link in its browser profile. A successful sign-in becomes active and is saved under the requested name. Use `add NAME --sso` for an explicit SSO flow.

You can save additional accounts the same way. Names start with a lowercase letter and contain up to 32 lowercase letters, digits, hyphens, or underscores.

### Switch

```powershell
claude-account use work
claude-account list
```

The asterisk in `list` marks the saved account matching Claude's active email and organization. Run `claude-account` without arguments for a terminal picker. A valid saved login works without another browser sign-in.

For a one-click switch, open the folder where you want the shortcut and run `claude-account shortcut work`. Double-click **Use Claude work.cmd** to select that account. Create a shortcut for each profile you use. A shortcut contains the local interpreter and script paths plus the profile name.

A request already sent uses its original account. New sessions use the selected login. In a local test with simulated subscription credentials and the installed Windows Claude CLI, running workers changed accounts on the next turn while retaining their processes and conversations. Claude Code controls credential caching, and its storage behavior may change between versions.

### Remove a saved profile

```powershell
claude-account remove lab
```

This deletes the named login from Windows Credential Manager. If it is the active profile, Claude and your running Herdr sessions stay signed in. To switch to another saved account and forget the old one, run `claude-account use work` **before** `claude-account remove lab`. Otherwise, the usual unsaved-login check asks you to name the active login before switching away. You can delete any shortcut you made for the removed name.

## If you use Claude's usual login

Using `/login` or `claude auth login` directly makes the browser-selected account active. Use `claude-account list` to see it. If it is already saved, you can switch away normally. If it is unfamiliar, run `claude-account save NEW-NAME` before switching away. The tool refuses to assign an existing name to a different identity.

If a saved login expires or is revoked, use `claude-account add NAME` and sign back into the same account. If an account switch is interrupted, `list` tells you which `use NAME` command to repeat. If a subsequent external login or refresh prevents recovery, `claude-account recover-login` opens Claude's browser flow to establish the active login.

## Credential handling

- Named profiles contain subscription OAuth credentials and account metadata. They are saved as **Generic Credentials** under `ClaudeAccountCLI/v1/profile/` in Windows Credential Manager for the current Windows user. Treat them as sensitive. Software running as that user may be able to access them.
- Switching updates the account-specific portions of Claude's native login files while retaining unrelated settings and MCP credentials. The tool uses local locks and records an interrupted switch in Credential Manager for recovery. Claude performs browser authentication and token renewal.
- For support, share the command name and a redacted error. Keep credentials, login codes, transcripts, and account files private. `list` output includes email and organization details. Generated shortcuts contain machine-specific paths.
- To sign the active session out, use Claude's own logout command. To uninstall the CLI, remove saved profiles with `claude-account remove NAME`, then delete the installed `claude-account.cmd`, its `ClaudeAccountCLI` program folder, and any shortcuts you created.

See [Claude Code authentication](https://code.claude.com/docs/en/authentication) for account types and login restrictions.

## Run the tests

On Windows, from the repository root:

```powershell
python -B -m unittest discover -s tests -v
```

The tests use invented account records, a temporary folder, and an in-memory substitute for Credential Manager. They exercise profile switching, direct logins, and recovery without enrolling a real account.
