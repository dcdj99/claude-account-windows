"""Small terminal menus and shortcuts for native Claude conversation actions."""
import sys
from pathlib import Path


def choose(title, rows):
    if not sys.stdin.isatty():
        raise RuntimeError("Use an interactive terminal, or supply an account: claude-account resume work [conversation].")
    print(title)
    for index, (value, description) in enumerate(rows, 1):
        print(f"  {index}. {description}")
    while True:
        try:
            answer = input("Choose a number or name (Enter to cancel): ").strip()
        except EOFError:
            answer = ""
        if not answer or answer.casefold() in {"q", "quit", "cancel"}:
            print("Cancelled.")
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(rows):
            return rows[int(answer) - 1][0]
        if answer in {value for value, _ in rows}:
            return answer
        print("Choose one of the listed entries, or press Enter to cancel.")


def choose_account(names, title="Choose the account for this launch:"):
    names = sorted(names)
    if not names:
        raise RuntimeError("No saved accounts. Use 'claude-account save NAME' or 'claude-account add NAME --isolated'.")
    return choose(title, [(name, name) for name in names])


def choose_action():
    return choose("Claude accounts", [("resume", "Resume using Claude's session picker"),
                                     ("resume-id", "Resume by session ID or name"), ("start", "Start a conversation"),
                                     ("continue", "Continue the latest conversation"),
                                     ("renew", "Renew a saved account"),
                                     ("use", "Change the shared default account"),
                                     ("list", "List saved accounts"), ("sessions", "Show running launches")])


def conversation_reference():
    try:
        value = input("Session ID, conversation name or transcript path (Enter to cancel): ").strip()
    except EOFError:
        value = ""
    return value or None


def run_menu(manager, native):
    from claude_account_windows import operation_lock
    while True:
        action = choose_action()
        if action is None:
            return 0
        if action == "list":
            manager.listing()
            continue
        if action == "sessions":
            native.reap()
            native.active(display=True)
            continue
        title = {"renew": "Choose the account to renew:",
                 "use": "Choose the shared default account:"}.get(action, "Choose the account for this launch:")
        account = choose_account(manager.vault.names(), title)
        if account is None:
            continue
        if action == "renew":
            native.enroll(account, renew=True)
            continue
        if action == "use":
            manager.check_files()
            with operation_lock(manager.config):
                manager.use(account)
            continue
        conversation = None
        if action == "resume-id":
            conversation = conversation_reference()
            if conversation is None:
                continue
            action = "resume"
        result = native.run(account, Path.cwd(), launch_arguments(action, conversation=conversation))
        if result:
            return result


def launch_arguments(action, *, conversation=None, title=None, extra=()):
    from claude_native import native_args
    forwarded = native_args(extra)
    selectors = {"--name", "-n", "--resume", "-r", "--continue", "-c", "--fork-session"}
    if any(value in selectors for value in forwarded):
        raise RuntimeError("Use the shortcut's positional conversation/name, or 'claude-account run ACCOUNT -- ...' for native flags.")
    if action == "start":
        result = ["--name", title] if title else []
    elif action == "resume":
        result = ["--resume"] + ([conversation] if conversation else [])
    elif action == "continue":
        result = ["--continue"]
    elif action == "fork":
        result = ["--resume", conversation, "--fork-session", "--name", title]
    else:
        raise ValueError("Unknown conversation action")
    return result + forwarded
