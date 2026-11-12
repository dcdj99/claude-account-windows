"""Small terminal menus and shortcuts for native Claude conversation actions."""
import sys


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


def choose_account(names):
    names = sorted(names)
    if not names:
        raise RuntimeError("No saved accounts. Use 'claude-account save NAME' or 'claude-account add NAME --isolated'.")
    return choose("Choose the account for this launch (shared default is unchanged):", [(name, name) for name in names])


def choose_action():
    return choose("Claude accounts", [("resume", "Resume a conversation"), ("start", "Start a conversation"),
                                     ("continue", "Continue the latest conversation"),
                                     ("use", "Change the shared default account"),
                                     ("list", "List saved accounts"), ("sessions", "Show running launches")])


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
