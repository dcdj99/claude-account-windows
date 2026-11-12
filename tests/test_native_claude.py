"""Installed-Claude integration: invented OAuth credentials, loopback-only API, no real account calls."""
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from claude_account import private_json
from claude_sessions import Sessions, invoke_claude
from test_accounts import MemoryVault
from test_sessions import profile


@unittest.skipUnless(os.environ.get("CLAUDE_ACCOUNT_NATIVE_TEST") == "1", "opt-in installed Claude integration")
class NativeClaudeTests(unittest.TestCase):
    def test_pinned_account_and_transcript_survive_switches_and_shared_mode(self):
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
                observed.append({"account": known.get(token, "UNEXPECTED"), "markers": sorted(set(markers))})
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
        env.update(USERPROFILE=str(home), HOME=str(home), APPDATA=str(root / "roaming"), LOCALAPPDATA=str(root / "local"))
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
                                                "cachedGrowthBookFeatures": {"tengu_windows_credman": False}})
            private_json(home / ".claude" / ".credentials.json", {"claudeAiOauth": saved["oauth"]})
        try:
            with patch.dict(os.environ, env, clear=True):
                shared("personal")
                manager = Sessions(home, root / "managed", vault, exe, invoke)
                manager.run("billing", "work", project, ["--model", "sonnet", "-p", "journey-start"])
                shared("lab")
                manager.resume("billing", args=["-p", "journey-pinned"])
                manager.run("docs", "work", project, ["--model", "sonnet", "-p", "journey-docs"])
                manager.resume("billing", account="personal", args=["-p", "journey-switched"])
                manager.resume("docs", args=["-p", "journey-independent"])
                manager.resume("billing", shared=True, args=["-p", "journey-shared"])
                manager.resume("billing", account="work", args=["-p", "journey-repinned"])
                self.assertEqual([o["account"] for o in observed], ["work", "work", "work", "personal", "work", "lab", "work"])
                self.assertEqual(observed[-1]["markers"], sorted(["journey-start", "journey-pinned", "journey-switched", "journey-shared", "journey-repinned"]))
                self.assertEqual(observed[4]["markers"], ["journey-docs", "journey-independent"])
                self.assertTrue((manager.folder("billing") / "active.json").exists(), "Native lifecycle hook did not execute")
                manager.run("live", "work", project, ["--model", "sonnet", "-p", "--input-format", "stream-json",
                                                       "--output-format", "stream-json", "--verbose"])
                self.assertEqual([o["account"] for o in observed[-2:]], ["work", "work"])
                self.assertIn("journey-livefirst", observed[-1]["markers"])
                allow_failure = True
                before_denial = len(observed)
                result = manager.resume("billing", account="lab", args=["-p", "journey-denied"])
                self.assertNotEqual(result, 0)
                self.assertEqual(manager.record("billing")["profile"], "lab")
                self.assertTrue(observed[before_denial:])
                self.assertTrue(all(o["account"] == "lab" for o in observed[before_denial:]))
                (root / "request-witness.json").write_text(json.dumps(observed, indent=2), encoding="utf-8")
                print("Native request witness:", root / "request-witness.json", flush=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
