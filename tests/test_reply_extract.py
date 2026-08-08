"""Stdlib-only test for the bin/agent-reply-extract CLI.

The CLI prints the new assistant reply since a stored byte offset and advances
that offset, so reply extraction can run on the worker's machine (where the
transcript lives) instead of in the bridge. Run as a subprocess to exercise the
real importlib-loads-the-bridge path.

Run: python3 -m unittest tests.test_reply_extract -v
"""

import os
import json
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(HERE, "bin", "agent-reply-extract")


class ReplyExtractCliTests(unittest.TestCase):
    def test_extracts_and_advances_offset(self):
        # minimal Claude transcript with one assistant text message
        tx = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        tx.write('{"type":"assistant","message":{"content":[{"type":"text","text":"hello"}]}}\n')
        tx.close()
        self.addCleanup(os.unlink, tx.name)
        off = tx.name + ".off"
        self.addCleanup(lambda: os.path.exists(off) and os.unlink(off))

        out = subprocess.run(
            [sys.executable, CLI, tx.name, off], capture_output=True, text=True
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("hello", out.stdout)
        self.assertTrue(os.path.exists(off))  # offset persisted

        # the offset must have advanced past the consumed line
        with open(off) as f:
            saved = json.load(f)
        self.assertEqual(saved["transcript"], os.path.realpath(tx.name))
        self.assertGreater(saved["offset"], 0)

        # second run with the advanced offset yields nothing new
        out2 = subprocess.run(
            [sys.executable, CLI, tx.name, off], capture_output=True, text=True
        )
        self.assertEqual(out2.returncode, 0, out2.stderr)
        self.assertEqual(out2.stdout.strip(), "")

    def test_new_transcript_resets_shared_offset_file(self):
        with tempfile.TemporaryDirectory() as root:
            off = os.path.join(root, "reply-relay.off")
            first = os.path.join(root, "first.jsonl")
            second = os.path.join(root, "second.jsonl")
            for path, reply in ((first, "one"), (second, "two")):
                with open(path, "w") as f:
                    f.write(json.dumps({
                        "type": "assistant",
                        "message": {"content": [
                            {"type": "text", "text": reply}
                        ]},
                    }) + "\n")
            subprocess.run([sys.executable, CLI, first, off], check=True,
                           capture_output=True, text=True)
            out = subprocess.run(
                [sys.executable, CLI, second, off], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout.strip(), "two")

    def test_truncated_transcript_resets_offset_beyond_eof(self):
        with tempfile.TemporaryDirectory() as root:
            tx = os.path.join(root, "session.jsonl")
            off = os.path.join(root, "reply-relay.off")
            with open(tx, "w") as f:
                f.write(json.dumps({
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "long"}]},
                }) + "\n")
            subprocess.run([sys.executable, CLI, tx, off], check=True,
                           capture_output=True, text=True)
            with open(tx, "w") as f:
                f.write(json.dumps({
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "new"}]},
                }) + "\n")
            out = subprocess.run(
                [sys.executable, CLI, tx, off], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout.strip(), "new")


if __name__ == "__main__":
    unittest.main()
