"""Integration-light tests for the standalone control-plane CLIs."""
import json
import hashlib
import hmac
import os
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer


HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE_CTL = os.path.join(HERE, "bin", "bridge-ctl")
SESSION_USAGE = os.path.join(HERE, "bin", "agent-session-usage")


class BridgeCtlTests(unittest.TestCase):
    def invoke_signed(self, args):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                size = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(size)
                received.append((body, self.headers.get("X-Webhook-Signature")))
                response = b'{"status":"ok"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, _format, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.timeout = 5
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as root:
                credentials = os.path.join(root, "bridge-webhook")
                secret = "test-secret"
                with open(credentials, "w") as f:
                    f.write(
                        f"BRIDGE_WEBHOOK_URL=http://127.0.0.1:{server.server_port}/event\n"
                        f"BRIDGE_WEBHOOK_SECRET={secret}\n"
                    )
                env = os.environ.copy()
                env["CLAUDE_WORKERS_BRIDGE_WEBHOOK_FILE"] = credentials
                out = subprocess.run(
                    [BRIDGE_CTL, *args], env=env,
                    capture_output=True, text=True, timeout=10)
            thread.join(timeout=5)
        finally:
            server.server_close()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(len(received), 1)
        body, signature = received[0]
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertEqual(signature, expected)
        return json.loads(body)

    def test_machines_lists_local_and_remote_repo_counts(self):
        with tempfile.TemporaryDirectory() as root:
            config = os.path.join(root, "config.json")
            with open(config, "w") as f:
                json.dump({
                    "machines": {"mac": {"ssh": "me@mac"}},
                    "repos": {
                        "1": {"name": "local-app", "dir": "/a"},
                        "2": {"name": "ios", "dir": "/b", "host": "mac"},
                    },
                }, f)
            env = os.environ.copy()
            env["CLAUDE_BRIDGE_CONFIG"] = config
            out = subprocess.run(
                [BRIDGE_CTL, "machines"], env=env,
                capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn("local", out.stdout)
            self.assertIn("mac", out.stdout)
            self.assertIn("me@mac", out.stdout)
            self.assertEqual(out.stdout.count("1 repo(s)"), 2)

    def test_help_exposes_health_cost_and_lifecycle_commands(self):
        out = subprocess.run(
            [BRIDGE_CTL, "help"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        for command in ("health", "cost", "restart", "harness", "machines"):
            self.assertIn(command, out.stdout)

    def test_status_and_model_build_worker_control_events(self):
        self.assertEqual(self.invoke_signed(["status", "app"]), {
            "event_type": "claude.bridge.worker_control",
            "action": "status", "name": "app",
        })
        self.assertEqual(self.invoke_signed(["model", "app", "opus"]), {
            "event_type": "claude.bridge.worker_control",
            "action": "model", "name": "app", "model": "opus",
        })

    def test_harness_no_handoff_is_boolean_false(self):
        self.assertEqual(
            self.invoke_signed(["harness", "app", "codex", "--no-handoff"]),
            {"event_type": "claude.bridge.worker_control",
             "action": "harness", "name": "app", "engine": "codex",
             "handoff": False},
        )

    def test_addmachine_builds_signed_config_event(self):
        self.assertEqual(
            self.invoke_signed(["addmachine", "mac", "you@mac"]),
            {"event_type": "claude.bridge.machine_set", "action": "set",
             "name": "mac", "ssh": "you@mac"},
        )


class SessionUsageCliTests(unittest.TestCase):
    def test_directory_mode_finds_claude_session(self):
        with tempfile.TemporaryDirectory() as home:
            project = os.path.join(home, ".claude", "projects", "-work-repo")
            os.makedirs(project)
            transcript = os.path.join(project, "session.jsonl")
            with open(transcript, "w") as f:
                f.write(json.dumps({
                    "type": "assistant",
                    "message": {"model": "claude-opus-5", "usage": {
                        "input_tokens": 3, "output_tokens": 9}},
                }) + "\n")
            env = os.environ.copy()
            env["HOME"] = home
            out = subprocess.run(
                [SESSION_USAGE, "claude", "--directory", "/work/repo"],
                env=env, capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            payload = json.loads(out.stdout)
            self.assertEqual(payload["transcript_path"], transcript)
            self.assertEqual(payload["engine"], "claude")

    def test_directory_mode_uses_exit_one_for_no_session(self):
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            out = subprocess.run(
                [SESSION_USAGE, "codex", "--directory", "/never/ran"],
                env=env, capture_output=True, text=True)
            self.assertEqual(out.returncode, 1)
            self.assertEqual(out.stdout, "")


if __name__ == "__main__":
    unittest.main()
