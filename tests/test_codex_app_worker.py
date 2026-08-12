"""Unit and protocol tests for the Codex app-server worker."""

import asyncio
import importlib.util
import json
import os
import tempfile
import textwrap
import unittest
from importlib.machinery import SourceFileLoader
from types import SimpleNamespace


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_worker():
    path = os.path.join(ROOT, "bin", "codex-app-worker")
    loader = SourceFileLoader("codex_app_worker", path)
    spec = importlib.util.spec_from_loader("codex_app_worker", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


worker_mod = load_worker()


class PureHelperTests(unittest.TestCase):
    def test_redact_command_hides_common_credentials(self):
        command = (
            "curl --client-secret hunter2 -H 'Authorization: Bearer bearer-value' "
            "https://x API_KEY=secret DISCORD_TOKEN=discord-value "
            "password='also-secret'"
        )
        redacted = worker_mod.redact_command(command)
        self.assertNotIn("hunter2", redacted)
        self.assertNotIn("also-secret", redacted)
        self.assertNotIn("bearer-value", redacted)
        self.assertNotIn("discord-value", redacted)
        self.assertNotIn("=secret", redacted)
        self.assertGreaterEqual(redacted.count("[redacted]"), 3)

    def test_diff_stats_counts_content_not_headers(self):
        diff = "--- a/a.py\n+++ b/a.py\n-old\n+new\n+++ b/b.py\n+added\n"
        self.assertEqual(worker_mod.diff_stats(diff), {
            "files": 2, "additions": 2, "deletions": 1,
        })

    def test_usage_totals_subtracts_cache_and_prices_reasoning_as_output(self):
        totals = worker_mod.usage_totals({"total": {
            "inputTokens": 100,
            "cachedInputTokens": 70,
            "cacheWriteInputTokens": 4,
            "outputTokens": 8,
            "reasoningOutputTokens": 3,
        }})
        self.assertEqual(totals, {
            "input": 30, "cache_read": 70, "cache_write": 4, "output": 11,
        })

    def test_activity_summary_never_includes_command_output(self):
        item = {
            "type": "commandExecution",
            "command": "echo API_KEY=secret",
            "aggregatedOutput": "sensitive stdout",
            "exitCode": 0,
        }
        summary = worker_mod.summarize_item(item, completed=True)
        self.assertIn("[redacted]", summary)
        self.assertNotIn("sensitive stdout", summary)


FAKE_APP_SERVER = r'''#!/usr/bin/env python3
import json
import sys


def send(value):
    print(json.dumps(value, separators=(",", ":")), flush=True)


thread = {
    "id": "thread-test",
    "path": "/tmp/rollout-test.jsonl",
    "status": {"type": "idle"},
}
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    request_id = request["id"]
    method = request.get("method")
    if method == "initialize":
        result = {"codexHome": "/tmp/codex-test"}
    elif method == "model/list":
        result = {"data": [{
            "id": "gpt-test", "defaultReasoningEffort": "high",
        }]}
    elif method in ("thread/start", "thread/resume"):
        result = {
            "thread": thread,
            "model": "gpt-test",
            "modelProvider": "openai",
            "reasoningEffort": "high",
        }
    elif method == "thread/name/set":
        result = {}
    elif method == "thread/goal/get":
        result = {"goal": None}
    elif method == "account/rateLimits/read":
        result = {"rateLimits": {"primary": {
            "usedPercent": 5, "windowDurationMins": 60, "resetsAt": 1,
        }}}
    elif method == "turn/start":
        turn = {"id": "turn-test", "status": "inProgress"}
        send({"id": request_id, "result": {"turn": turn}})
        send({"method": "turn/started", "params": {"turn": turn}})
        send({"method": "turn/plan/updated", "params": {"plan": [
            {"step": "Test protocol", "status": "inProgress"},
        ]}})
        send({"method": "item/reasoning/summaryTextDelta", "params": {
            "delta": "Checking the protocol",
        }})
        send({"method": "item/completed", "params": {"item": {
            "type": "agentMessage", "phase": "final_answer", "text": "OK",
        }}})
        send({"method": "thread/tokenUsage/updated", "params": {
            "tokenUsage": {"total": {
                "inputTokens": 10, "cachedInputTokens": 4,
                "outputTokens": 2, "reasoningOutputTokens": 1,
            }},
        }})
        send({"method": "turn/completed", "params": {"turn": {
            "id": "turn-test", "status": "completed", "error": None,
        }}})
        continue
    elif method == "turn/steer":
        result = {"turnId": "turn-test"}
    elif method == "turn/interrupt":
        result = {}
    else:
        send({"id": request_id, "error": {
            "code": -32601, "message": "unsupported " + str(method),
        }})
        continue
    send({"id": request_id, "result": result})
'''


class WorkerProtocolTests(unittest.IsolatedAsyncioTestCase):
    def write_fake(self, root):
        fake = os.path.join(root, "fake-codex")
        with open(fake, "w", encoding="utf-8") as handle:
            handle.write(textwrap.dedent(FAKE_APP_SERVER))
        os.chmod(fake, 0o755)
        return fake

    def args(self, root, fake, name="test-worker"):
        return SimpleNamespace(
            name=name,
            dir=root,
            state_dir=os.path.join(root, "state"),
            chat="",
            resume=False,
            codex_command=fake,
            strict_config=False,
        )

    async def test_worker_runs_turn_and_persists_structured_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = self.write_fake(tmp)
            worker = worker_mod.CodexAppWorker(self.args(tmp, fake))
            run_task = asyncio.create_task(worker.run())
            for _ in range(100):
                if worker.state.get("ready"):
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(worker.state.get("ready"))

            fast = await worker_mod.control_request(
                str(worker.socket_path),
                {"command": "send", "text": "/fast", "typed": True},
            )
            model = await worker_mod.control_request(
                str(worker.socket_path),
                {"command": "send", "text": "/model gpt-test", "typed": True},
            )
            self.assertTrue(fast["fast"])
            self.assertEqual(model["model"], "gpt-test")

            result = await worker_mod.control_request(
                str(worker.socket_path), {"command": "send", "text": "hello"}
            )
            self.assertTrue(result["ok"])
            for _ in range(100):
                if worker.state.get("last_completed_turn_id") == "turn-test":
                    break
                await asyncio.sleep(0.01)

            self.assertEqual(worker.final_reply, "OK")
            self.assertEqual(worker.state["last_turn_status"], "completed")
            self.assertEqual(worker.state["status"], "idle")
            self.assertFalse((worker.state_dir / worker_mod.ACTIVE_MARKER).exists())
            self.assertEqual(worker.state["plan"][0]["step"], "Test protocol")
            self.assertEqual(
                worker_mod.usage_totals(worker.state["token_usage"])["output"], 3
            )
            with open(worker.state_file, encoding="utf-8") as handle:
                persisted = json.load(handle)
            self.assertEqual(persisted["thread_id"], "thread-test")
            self.assertEqual(persisted["transcript_path"], "/tmp/rollout-test.jsonl")

            worker.stop_event.set()
            self.assertEqual(await asyncio.wait_for(run_task, 5), 0)

    async def test_worker_exits_when_app_server_crashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = self.write_fake(tmp)
            worker = worker_mod.CodexAppWorker(
                self.args(tmp, fake, name="crash-worker")
            )
            run_task = asyncio.create_task(worker.run())
            for _ in range(100):
                if worker.state.get("ready"):
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(worker.state.get("ready"))

            worker.client.proc.terminate()
            result = await asyncio.wait_for(run_task, 5)
            self.assertNotEqual(result, 0)
            self.assertEqual(worker.state["status"], "failed")
            self.assertIn("exited unexpectedly", worker.state["last_error"]["message"])

    async def test_active_turn_routes_new_input_to_steer(self):
        class RecordingClient:
            def __init__(self):
                self.call = None

            async def request(self, method, params, timeout=60):
                self.call = (method, params)
                return {"turnId": "turn-active"}

        with tempfile.TemporaryDirectory() as tmp:
            worker = worker_mod.CodexAppWorker(
                self.args(tmp, "/unused", name="steer-worker")
            )
            client = RecordingClient()
            worker.client = client
            worker.state.update({
                "thread_id": "thread-test",
                "active_turn_id": "turn-active",
                "status": "active",
            })
            result = await worker.submit("change direction")
            self.assertEqual(result["mode"], "steer")
            self.assertEqual(client.call[0], "turn/steer")
            self.assertEqual(client.call[1]["expectedTurnId"], "turn-active")
            self.assertEqual(client.call[1]["input"][0]["text"], "change direction")


if __name__ == "__main__":
    unittest.main()
