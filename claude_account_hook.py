"""Record only lifecycle identity and transcript location; never record prompts or credentials."""
import json
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_account_windows import private_json, reject_links


def record_event(destination, config, launch_id, event, source=None):
    if event.get("hook_event_name") not in {"SessionStart", "Stop", "SessionEnd", "CwdChanged"}:
        return
    sid = str(uuid.UUID(event["session_id"]))
    transcript = Path(event["transcript_path"])
    root = Path(config).resolve() / "projects"
    imported = bool(source) and transcript.resolve() == Path(source).resolve()
    if not transcript.is_absolute() or (not transcript.resolve().is_relative_to(root) and not imported):
        raise ValueError("Transcript is outside the managed configuration.")
    if transcript.name != sid + ".jsonl":
        raise ValueError("Transcript does not match the session identity.")
    destination = Path(destination)
    reject_links(destination)
    private_json(destination, {"launch_id": launch_id, "session_id": sid,
                              "transcript": str(transcript.resolve()), "project": event["cwd"]})


if __name__ == "__main__":
    try:
        record_event(*sys.argv[1:4], json.load(sys.stdin), source=sys.argv[4] if len(sys.argv) > 4 else None)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        print("Could not record the managed chat lifecycle. Check 'claude-account sessions'.", file=sys.stderr)
        sys.exit(1)
