"""Public command routing with fake accounts and no real Claude launches."""
from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import claude_account
from claude_ui import choose_account, launch_arguments


class CommandJourneyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Mock()
        self.vault.names.return_value = ["personal", "work"]
        self.accounts = Mock(vault=self.vault)
        self.native = Mock()
        self.native.run.return_value = 0

    def command(self, argv, answers=(), interactive=True):
        with patch.object(sys, "argv", ["claude-account", *argv]), \
             patch("claude_account.Vault", return_value=self.vault), \
             patch("claude_account.Accounts", return_value=self.accounts), \
             patch("claude_native.NativeLauncher", return_value=self.native), \
             patch("claude_account.shutil.which", return_value="fake-claude.exe"), \
             patch("claude_account.Path.home", return_value=Path(self.tmp.name)), \
             patch.dict(os.environ, {"LOCALAPPDATA": self.tmp.name}, clear=True), \
             patch("sys.stdin.isatty", return_value=interactive), \
             patch("builtins.input", side_effect=list(answers)), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return claude_account.main()

    def test_resume_without_flags_chooses_account_then_native_picker(self):
        self.assertEqual(self.command(["resume"], ["work"]), 0)
        self.native.run.assert_called_once_with("work", Path.cwd(), ["--resume"])
        self.accounts.use.assert_not_called()

    def test_direct_commands_need_no_interactive_terminal(self):
        cases = [(["start", "work"], "work", []),
                 (["start", "work", "Billing report"], "work", ["--name", "Billing report"]),
                 (["resume", "personal"], "personal", ["--resume"]),
                 (["resume", "personal", "billing"], "personal", ["--resume", "billing"]),
                 (["continue", "work"], "work", ["--continue"]),
                 (["fork", "personal", "billing", "experiment"], "personal",
                  ["--resume", "billing", "--fork-session", "--name", "experiment"])]
        for argv, account, args in cases:
            self.native.reset_mock()
            self.assertEqual(self.command(argv, interactive=False), 0)
            self.native.run.assert_called_once_with(account, Path.cwd(), args)
        self.accounts.use.assert_not_called()

    def test_no_argument_menu_resumes_without_changing_shared_default(self):
        self.command([], ["1", "2"])
        self.native.run.assert_called_once_with("work", Path.cwd(), ["--resume"])
        self.accounts.use.assert_not_called()

    def test_cancel_and_invalid_account_do_not_fall_back_to_shared(self):
        self.command(["resume"], ["not-saved", ""])
        self.native.run.assert_not_called()
        self.accounts.use.assert_not_called()

    def test_missing_account_in_noninteractive_input_is_actionable(self):
        with self.assertRaisesRegex(RuntimeError, "resume work"):
            self.command(["resume"], interactive=False)
        self.native.run.assert_not_called()

    def test_shortcut_options_cannot_override_selected_conversation(self):
        for extra in (["--resume", "other"], ["--continue"], ["--name=other"], ["--fork-session"]):
            with self.assertRaisesRegex(RuntimeError, "run"):
                launch_arguments("resume", conversation="billing", extra=extra)
        self.assertEqual(launch_arguments("resume", conversation="billing", extra=["-p", "review"]),
                         ["--resume", "billing", "-p", "review"])

    def test_legacy_commands_and_flags_are_removed(self):
        for argv in (["migrate-sessions"], ["delete-session", "billing", "--yes"],
                     ["sessions", "--legacy"], ["resume", "billing", "--account", "work"],
                     ["run", "work", "--name", "billing"]):
            with self.assertRaises(SystemExit) as error:
                self.command(argv)
            self.assertNotEqual(error.exception.code, 0)

    def test_empty_catalog_never_uses_shared_login_implicitly(self):
        with self.assertRaisesRegex(RuntimeError, "save|add"):
            choose_account([])

    def test_advanced_run_preserves_native_options(self):
        self.command(["run", "work", "--", "--resume", "billing", "--model", "sonnet"], interactive=False)
        self.native.run.assert_called_once_with("work", Path.cwd(), ["--resume", "billing", "--model", "sonnet"])
