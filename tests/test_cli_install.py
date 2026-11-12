"""Exercise the real CLI installer only inside an isolated pretend Windows home."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class InstallationTests(unittest.TestCase):
    def test_installed_launcher_packages_runtime_and_preserves_unrelated_launcher(self):
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            bin_dir = home / ".local" / "bin"
            bin_dir.mkdir(parents=True)
            env = dict(os.environ, USERPROFILE=str(home), LOCALAPPDATA=str(Path(tmp) / "local"),
                       PATH=str(bin_dir) + os.pathsep + os.environ["PATH"])
            result = subprocess.run([sys.executable, "-I", "-B", str(source / "claude_account.py"), "install"],
                                    env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            launcher = bin_dir / "claude-account.cmd"
            installed = list((Path(tmp) / "local" / "Programs" / "ClaudeAccountCLI").iterdir())
            self.assertEqual(len(installed), 1)
            for name in ("claude_account.py", "claude_account_windows.py", "claude_sessions.py", "claude_account_hook.py"):
                self.assertEqual((installed[0] / name).read_bytes(), (source / name).read_bytes())
            help_result = subprocess.run([sys.executable, "-I", "-B", str(installed[0] / "claude_account.py"), "resume", "--help"],
                                         env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(help_result.returncode, 0)
            self.assertIn("--shared", help_result.stdout)
            launcher.write_bytes(b"@rem unrelated user command\r\n")
            refused = subprocess.run([sys.executable, "-I", "-B", str(source / "claude_account.py"), "install"],
                                     env=env, capture_output=True, text=True, timeout=15)
            self.assertNotEqual(refused.returncode, 0)
            self.assertEqual(launcher.read_bytes(), b"@rem unrelated user command\r\n")
