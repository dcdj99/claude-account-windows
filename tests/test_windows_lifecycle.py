"""Real Windows lease inheritance and lifecycle-hook boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

from claude_account_hook import record_event
from claude_account_windows import SessionLease, private_directory


class WindowsLifecycleTests(unittest.TestCase):
    def test_child_keeps_session_busy_after_parent_releases_its_handle(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / ".lease"
            child = None
            try:
                with SessionLease(lock) as lease:
                    startup = subprocess.STARTUPINFO()
                    os.set_handle_inheritable(lease.handle, True)
                    startup.lpAttributeList = {"handle_list": [lease.handle]}
                    child = subprocess.Popen([sys.executable, "-I", "-B", "-c",
                                              "import sys; print('ready', flush=True); sys.stdin.readline()"],
                                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                             startupinfo=startup, close_fds=True)
                    os.set_handle_inheritable(lease.handle, False)
                    self.assertEqual(child.stdout.readline().strip(), "ready")
                with self.assertRaisesRegex(RuntimeError, "running|busy"):
                    with SessionLease(lock):
                        self.fail("Inherited handle did not hold the chat lease")
                child.communicate("done\n", timeout=10)
                with SessionLease(lock):
                    pass
            finally:
                if child and child.poll() is None:
                    child.kill()
                    child.wait()

    def test_hook_records_only_identity_and_rejects_external_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config"
            private_directory(config)
            destination = Path(tmp) / "active.json"
            sid = str(uuid.uuid4())
            event = {"hook_event_name": "SessionStart", "session_id": sid,
                     "transcript_path": str(config / "projects" / "project" / (sid + ".jsonl")),
                     "cwd": str(Path(tmp)), "prompt": "do not record this", "accessToken": "invented-secret"}
            record_event(destination, config, "launch-one", event)
            stored = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(stored["session_id"], sid)
            self.assertNotIn("do not record", destination.read_text(encoding="utf-8"))
            self.assertNotIn("invented-secret", destination.read_text(encoding="utf-8"))
            before = destination.read_bytes()
            event["transcript_path"] = str(Path(tmp) / (sid + ".jsonl"))
            with self.assertRaises(ValueError):
                record_event(destination, config, "launch-two", event)
            self.assertEqual(destination.read_bytes(), before)
