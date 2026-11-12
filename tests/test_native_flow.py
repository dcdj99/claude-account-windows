"""Installed-Claude integration: invented OAuth credentials, loopback-only API, no real account calls."""
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import claude_account
from claude_account import private_json
from claude_auth import invoke_claude
from claude_native import NativeLauncher
from test_accounts import MemoryVault
from support import profile


@unittest.skipUnless(os.environ.get("CLAUDE_ACCOUNT_NATIVE_TEST") == "1", "opt-in installed Claude integration")
class NativeFlowTests(unittest.TestCase):
    def test_native_names_resume_continue_fork_and_isolation(self):
        exe = str(Path(os.environ["USERPROFILE"]) / ".local/bin/claude.exe")
        root = Path(tempfile.mkdtemp(prefix="native-", dir=Path(__file__).resolve().parents[2]))
        home, project = root / "home", root / "project"
        (home / ".claude").mkdir(parents=True)
        project.mkdir()
        observed = []
        class Endpoint(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_CONNECT(self):
                self.send_error(502, "External connections disabled in test")
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path.split("?")[0] != "/v1/messages":
                    self.send_error(404)
                    return
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                known = {"invented-work": "work", "invented-personal": "personal", "invented-lab": "lab"}
                markers = re.findall(r"journey-[a-z]+", json.dumps(body.get("messages", [])))
                config_ids = sorted(set(re.findall(r"launches[\\/]+([0-9a-f]{32})", json.dumps(body.get("system", [])))))
                observed.append({"account": known.get(token, "UNEXPECTED"), "markers": sorted(set(markers)), "config_ids": config_ids})
                if "journey-denied" in markers:
                    payload = json.dumps({"type": "error", "error": {
                        "type": "rate_limit_error", "message": "Invented account limit for the isolation test"}}).encode()
                    self.send_response(429)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                message = {"id": "msg_" + uuid.uuid4().hex, "type": "message", "role": "assistant",
                           "model": body.get("model", "sonnet"), "content": [{"type": "text", "text": "received"}],
                           "stop_reason": "end_turn", "stop_sequence": None,
                           "usage": {"input_tokens": 10, "output_tokens": 2}}
                payload = json.dumps(message).encode()
                mime = "application/json"
                if body.get("stream"):
                    events = [("message_start", {"message": dict(message, content=[], stop_reason=None)}),
                              ("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}),
                              ("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "received"}}),
                              ("content_block_stop", {"index": 0}),
                              ("message_delta", {"delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 2}}),
                              ("message_stop", {})]
                    payload = "".join(f"event: {kind}\ndata: {json.dumps(dict(type=kind, **data))}\n\n" for kind, data in events).encode()
                    mime = "text/event-stream"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        env = {k: v for k, v in os.environ.items() if k.upper() in
               {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP", "PROGRAMFILES", "PROGRAMFILES(X86)"}}
        env.update(USERPROFILE=str(home), HOME=str(home), APPDATA=str(root / "roaming"), LOCALAPPDATA=str(root / "local"),
                   CLAUDE_ACCOUNT_PICKER_TEST=os.environ.get("CLAUDE_ACCOUNT_PICKER_TEST", "0"))
        allow_failure = False
        def invoke(args, *, env, cwd, capture=False, lease=None):
            isolated = dict(env, ANTHROPIC_BASE_URL=base, HTTP_PROXY=base, HTTPS_PROXY=base,
                            NO_PROXY="localhost,127.0.0.1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
                            DISABLE_AUTOUPDATER="1", CLAUDE_CODE_MAX_RETRIES="0")
            if args[1:3] != ["auth", "status"]:
                args = [args[0], "--setting-sources", "", "--tools", "", "--strict-mcp-config", *args[1:]]
            if "--input-format" in args:
                child = subprocess.Popen(args, env=isolated, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, encoding="utf-8")
                events = queue.Queue()
                def consume():
                    for line in child.stdout:
                        events.put(line)
                    events.put(None)
                reader = threading.Thread(target=consume, daemon=True)
                reader.start()
                try:
                    for marker in ("journey-livefirst", "journey-livesecond"):
                        if marker.endswith("second"):
                            shared("personal")
                        child.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": marker}}) + "\n")
                        child.stdin.flush()
                        while True:
                            line = events.get(timeout=30)
                            if line is None:
                                raise AssertionError("Streaming Claude exited before the reply")
                            try:
                                event = json.loads(line)
                            except ValueError:
                                continue
                            if event.get("type") == "result":
                                self.assertFalse(event.get("is_error"), event.get("result"))
                                break
                    child.stdin.close()
                    result = subprocess.CompletedProcess(args, child.wait(timeout=15))
                finally:
                    if child.poll() is None:
                        child.terminate()
                        child.wait(timeout=10)
                    reader.join(timeout=5)
                    child.stdout.close()
            else:
                result = invoke_claude(args, env=isolated, cwd=cwd, capture=capture, lease=lease)
            if not capture and result.returncode and not allow_failure:
                raise AssertionError("Native Claude returned a failure code: " + str(result.returncode))
            if not capture and not result.returncode and env.get("CLAUDE_ACCOUNT_MODE") == "native":
                expected_config = Path(env["CLAUDE_CONFIG_DIR"]).parent.name
                self.assertEqual(observed[-1]["config_ids"], [expected_config],
                                "Resumed system prompt retained a deleted launch directory")
            return result
        vault = MemoryVault()
        for name in ("work", "personal", "lab"):
            value = profile(name)
            value["account"]["accountUuid"] = str(uuid.uuid4())
            value["account"]["organizationUuid"] = str(uuid.uuid4())
            vault.put("profile/" + name, value)
        def shared(name):
            saved = vault.get("profile/" + name)
            private_json(home / ".claude.json", {"oauthAccount": saved["account"], "hasCompletedOnboarding": True,
                                                "projects": {str(project): {"hasTrustDialogAccepted": True,
                                                                           "hasCompletedProjectOnboarding": True}},
                                                "cachedGrowthBookFeatures": {"tengu_windows_credman": False}})
            private_json(home / ".claude" / ".credentials.json", {"claudeAiOauth": saved["oauth"]})
        try:
            with patch.dict(os.environ, env, clear=True):
                shared("personal")
                manager = NativeLauncher(home, root / "managed", vault, exe, invoke)
                def command(args):
                    with patch.object(sys, "argv", ["claude-account", *args]), \
                         patch("claude_account.Vault", return_value=vault), \
                         patch("claude_account.shutil.which", return_value=exe), \
                         patch("claude_account.Path.home", return_value=home), \
                         patch("claude_account.Path.cwd", return_value=project), \
                         patch("claude_native.NativeLauncher", return_value=manager):
                        return claude_account.main()
                if os.environ.get("CLAUDE_ACCOUNT_PICKER_TEST") == "1":
                    print("INTERACTIVE FIXTURE: send /exit after the seed reply; then choose picker-example, send journey-pickerresume, and /exit.", flush=True)
                    command(["start", "work", "picker-example", "--", "--model", "sonnet", "journey-pickerstart"])
                    self.assertEqual(observed[-1]["account"], "work")
                    with patch("builtins.input", return_value="personal"):
                        command(["resume"])
                    self.assertEqual(observed[-1]["account"], "personal")
                    self.assertIn("journey-pickerstart", observed[-1]["markers"])
                    self.assertIn("journey-pickerresume", observed[-1]["markers"])
                    self.assertEqual(list(manager.launches.iterdir()), [])
                    (root / "picker-witness.json").write_text(json.dumps(observed, indent=2), encoding="utf-8")
                    print("Native picker witness:", root / "picker-witness.json", flush=True)
                    return
                command(["start", "work", "billing", "--", "--model", "sonnet", "-p", "journey-start"])
                self.assertEqual(observed[-1]["account"], "work")
                self.assertFalse((manager.root / "sessions").exists())
                initial_transcripts = list(manager.history.rglob("*.jsonl"))
                self.assertEqual(len(initial_transcripts), 1)
                billing_transcript = initial_transcripts[0]
                command(["resume", "personal", "billing", "--", "-p", "journey-switched"])
                self.assertEqual(observed[-1]["account"], "personal")
                self.assertIn("journey-start", observed[-1]["markers"])
                command(["resume", "work", billing_transcript.stem, "--", "-p", "journey-id"])
                self.assertEqual(observed[-1]["account"], "work")
                self.assertIn("journey-switched", observed[-1]["markers"])
                command(["resume", "personal", str(billing_transcript), "--", "-p", "journey-path"])
                self.assertEqual(observed[-1]["account"], "personal")
                self.assertIn("journey-id", observed[-1]["markers"])
                shared("lab")
                command(["resume", "work", "billing", "--", "-p", "journey-work"])
                self.assertEqual(observed[-1]["account"], "work")
                command(["continue", "personal", "--", "-p", "journey-continue"])
                self.assertIn("journey-work", observed[-1]["markers"])
                self.assertEqual(observed[-1]["account"], "personal")
                command(["fork", "work", "billing", "side", "--", "-p", "journey-fork"])
                self.assertIn("journey-continue", observed[-1]["markers"])
                command(["resume", "personal", "billing", "--", "-p", "journey-original"])
                self.assertNotIn("journey-fork", observed[-1]["markers"])
                self.assertIn("journey-start", observed[-1]["markers"])
                command(["run", "work", "--", "--model", "sonnet", "-p", "--input-format", "stream-json",
                                               "--output-format", "stream-json", "--verbose"])
                self.assertEqual([o["account"] for o in observed[-2:]], ["work", "work"])
                self.assertIn("journey-livefirst", observed[-1]["markers"])
                invoke([exe, "--resume", "billing", "-p", "journey-shared"], env=manager.environment(), cwd=project)
                self.assertEqual(observed[-1]["account"], "personal")
                self.assertIn("journey-original", observed[-1]["markers"])
                self.assertEqual(observed[-1]["config_ids"], [])
                allow_failure = True
                before_denial = len(observed)
                result = command(["resume", "lab", "billing", "--", "-p", "journey-denied"])
                self.assertNotEqual(result, 0)
                self.assertTrue(observed[before_denial:])
                self.assertTrue(all(o["account"] == "lab" for o in observed[before_denial:]))
                self.assertEqual(list(manager.launches.iterdir()), [])
                self.assertTrue(list(manager.history.rglob("*.jsonl")))
                (root / "request-witness.json").write_text(json.dumps(observed, indent=2), encoding="utf-8")
                print("Native request witness:", root / "request-witness.json", flush=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
