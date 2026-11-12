"""Real Windows history junction safety; all paths are disposable."""
from pathlib import Path
import tempfile
import unittest

import claude_account_windows as win


class HistoryJunctionTests(unittest.TestCase):
    def test_shared_history_writes_and_link_only_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = root / "ordinary" / "projects"
            win.private_directory(history)
            config = root / "private"
            win.private_directory(config)
            link = config / "projects"
            win.create_history_junction(link, history)
            try:
                (link / "native-transcript.jsonl").write_text("synthetic history", encoding="utf-8")
                self.assertEqual((history / "native-transcript.jsonl").read_text(), "synthetic history")
                with self.assertRaises(RuntimeError):
                    win.reject_links(link)
                win.remove_history_junction(link, history)
                self.assertFalse(link.exists())
                self.assertTrue((history / "native-transcript.jsonl").exists())
            finally:
                if link.is_junction():
                    link.rmdir()

    def test_cleanup_refuses_real_directory_and_wrong_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("history", "other", "config"):
                win.private_directory(root / name)
            link = root / "config" / "projects"
            win.private_directory(link)
            with self.assertRaises(RuntimeError):
                win.remove_history_junction(link, root / "history")
            link.rmdir()
            win.create_history_junction(link, root / "other")
            try:
                with self.assertRaises(RuntimeError):
                    win.remove_history_junction(link, root / "history")
                self.assertTrue(link.is_junction())
            finally:
                link.rmdir()
