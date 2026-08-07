"""Stdlib-only test for the bin/agent-reply-extract CLI.

The CLI prints the new assistant reply since a stored byte offset and advances
that offset, so reply extraction can run on the worker's machine (where the
transcript lives) instead of in the bridge. Run as a subprocess to exercise the
real importlib-loads-the-bridge path.

Run: python3 -m unittest tests.test_reply_extract -v
"""

import os
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
            self.assertGreater(int(f.read().strip()), 0)

        # second run with the advanced offset yields nothing new
        out2 = subprocess.run(
            [sys.executable, CLI, tx.name, off], capture_output=True, text=True
        )
        self.assertEqual(out2.returncode, 0, out2.stderr)
        self.assertEqual(out2.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
