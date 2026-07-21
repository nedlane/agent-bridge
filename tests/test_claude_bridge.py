"""Stdlib-only unit tests for the pure helpers in bin/agent-bridge.

bin/agent-bridge has no .py extension and imports discord.py / aiohttp only
lazily inside run_bridge(), so we can load it with importlib and exercise the
pure helpers without those third-party packages installed.

Run: python3 -m unittest discover -s tests -v
"""

import hashlib
import hmac
import importlib.util
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_module():
    # bin/agent-bridge has no .py extension, so an explicit SourceFileLoader
    # is required — spec_from_file_location can't infer a loader by suffix.
    path = os.path.join(HERE, "bin", "agent-bridge")
    loader = SourceFileLoader("claude_bridge", path)
    spec = importlib.util.spec_from_loader("claude_bridge", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


cb = _load_module()


class VerifySignatureTests(unittest.TestCase):
    def _sig(self, body, secret):
        return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def test_correct_signature_passes(self):
        body = b'{"turn":"ended"}'
        secret = "topsecret"
        self.assertTrue(cb.verify_signature(body, self._sig(body, secret), secret))

    def test_tampered_body_fails(self):
        secret = "topsecret"
        sig = self._sig(b"original", secret)
        self.assertFalse(cb.verify_signature(b"tampered", sig, secret))

    def test_tampered_signature_fails(self):
        body = b"payload"
        secret = "topsecret"
        good = self._sig(body, secret)
        bad = ("0" if good[0] != "0" else "1") + good[1:]
        self.assertFalse(cb.verify_signature(body, bad, secret))

    def test_empty_header_returns_false(self):
        self.assertFalse(cb.verify_signature(b"payload", "", "secret"))

    def test_empty_secret_returns_false(self):
        body = b"payload"
        # Even a header that would match an empty-key HMAC must be rejected,
        # because the empty-secret short-circuit fires first.
        self.assertFalse(cb.verify_signature(body, self._sig(body, ""), ""))


class ChannelAllowsTests(unittest.TestCase):
    def test_welcome_channel_open_to_anyone(self):
        cfg = {"welcome_channel": 555, "allowed_users": [], "repos": {}}
        self.assertTrue(cb.channel_allows(cfg, 555, 99999))
        # welcome_channel comparison is string-normalized
        self.assertTrue(cb.channel_allows(cfg, "555", 99999))

    def test_allowed_user_allowed_in_any_channel(self):
        cfg = {"welcome_channel": None, "allowed_users": [42], "repos": {}}
        self.assertTrue(cb.channel_allows(cfg, 111, 42))
        self.assertTrue(cb.channel_allows(cfg, 222, 42))

    def test_guest_only_in_own_channel(self):
        cfg = {
            "welcome_channel": None,
            "allowed_users": [],
            "repos": {"100": {"name": "repo", "guests": [7]}},
        }
        self.assertTrue(cb.channel_allows(cfg, 100, 7))
        # guest of channel 100 has no rights in another channel
        self.assertFalse(cb.channel_allows(cfg, 200, 7))

    def test_viewer_or_unknown_denied(self):
        cfg = {
            "welcome_channel": None,
            "allowed_users": [],
            "repos": {"100": {"name": "repo", "guests": [7], "viewers": [8]}},
        }
        self.assertFalse(cb.channel_allows(cfg, 100, 8))   # viewer cannot drive
        self.assertFalse(cb.channel_allows(cfg, 100, 999))  # unknown
        self.assertFalse(cb.channel_allows(cfg, 300, 8))    # no such channel


class ProfileArgsTests(unittest.TestCase):
    PDIR = "/opt/profiles"

    def test_owner_empty_and_none_and_blank(self):
        self.assertEqual(cb.profile_args("owner", self.PDIR), [])
        self.assertEqual(cb.profile_args("", self.PDIR), [])
        self.assertEqual(cb.profile_args(None, self.PDIR), [])

    def test_utility_flags(self):
        args = cb.profile_args("utility", self.PDIR)
        self.assertIn("--enforce-perms", args)
        self.assertIn("--strict-mcp-config", args)
        # utility disables built-in tools via an explicit empty --tools value
        self.assertIn("--tools", args)
        i = args.index("--tools")
        self.assertEqual(args[i + 1], "")
        # deterministic profile-dir wiring
        self.assertIn(os.path.join(self.PDIR, "utility.mcp.json"), args)
        self.assertIn(os.path.join(self.PDIR, "utility.settings.json"), args)

    def test_greeter_flags(self):
        args = cb.profile_args("greeter", self.PDIR)
        self.assertIn("--enforce-perms", args)
        self.assertIn("--strict-mcp-config", args)
        self.assertIn(os.path.join(self.PDIR, "greeter.mcp.json"), args)
        self.assertIn(os.path.join(self.PDIR, "greeter.settings.json"), args)

    def test_collab_flags(self):
        # collab is full trust, same as owner: no flags, so it inherits
        # claude-launch's default --dangerously-skip-permissions and never
        # wedges on a permission prompt nobody can answer over Discord.
        self.assertEqual(cb.profile_args("collab", self.PDIR), [])

    def test_unknown_profile_empty(self):
        self.assertEqual(cb.profile_args("bogus", self.PDIR), [])


class RequestCardTests(unittest.TestCase):
    def test_round_trip(self):
        card = cb.format_request_card(1234567890, "myrepo", "please help")
        self.assertEqual(cb.parse_request_marker(card), (1234567890, "myrepo"))

    def test_resolved_card_parses_to_none(self):
        card = cb.format_request_card(42, "proj", "hi")
        # A resolved card keeps only the headline (marker stripped) — mimic that.
        headline = card.split("\n\n")[0]
        resolved = f"{headline}\n\n**denied** by <@1>"
        self.assertIsNone(cb.parse_request_marker(resolved))

    def test_none_and_garbage(self):
        self.assertIsNone(cb.parse_request_marker(None))
        self.assertIsNone(cb.parse_request_marker("no marker here"))

    def test_summary_default_when_blank(self):
        card = cb.format_request_card(9, "p", "   ")
        self.assertIn("(no details given)", card)


class CategoryMatchTests(unittest.TestCase):
    CATS = ["🤖 Meta & Infra", "🏎️ AV Stack (uqr_ws)", "💼 Business & Ops"]

    def test_normalize_strips_emoji_case_and_punct(self):
        self.assertEqual(cb.normalize_category("🏎️ AV Stack (uqr_ws)"), "avstackuqrws")
        self.assertEqual(
            cb.normalize_category("av stack uqr_ws"),
            cb.normalize_category("🏎️ AV Stack (uqr_ws)"),
        )

    def test_find_matches_ignoring_emoji_and_case(self):
        self.assertEqual(cb.find_category(self.CATS, "meta & infra"), "🤖 Meta & Infra")
        self.assertEqual(
            cb.find_category(self.CATS, "AV STACK (uqr_ws)"), "🏎️ AV Stack (uqr_ws)"
        )

    def test_find_returns_none_for_new_category(self):
        self.assertIsNone(cb.find_category(self.CATS, "MoTeC M1"))

    def test_find_none_for_blank_request(self):
        self.assertIsNone(cb.find_category(self.CATS, ""))
        self.assertIsNone(cb.find_category(self.CATS, "   "))
        self.assertIsNone(cb.find_category(self.CATS, None))


class SplitMessageTests(unittest.TestCase):
    def test_splits_on_line_boundaries(self):
        text = "aaa\nbbb\nccc"
        chunks = cb.split_message(text, limit=5)
        self.assertEqual(chunks, ["aaa", "bbb", "ccc"])
        for c in chunks:
            self.assertLessEqual(len(c), 5)

    def test_single_over_limit_line_hard_split(self):
        chunks = cb.split_message("abcdefghij", limit=4)
        for c in chunks:
            self.assertLessEqual(len(c), 4)
        self.assertEqual("".join(chunks), "abcdefghij")

    def test_truncation_at_reply_cap(self):
        text = "x" * (cb.REPLY_CAP + 500)
        chunks = cb.split_message(text)
        self.assertTrue(any("truncated" in c for c in chunks))
        # everything stays under Discord's per-message limit
        for c in chunks:
            self.assertLessEqual(len(c), cb.DISCORD_LIMIT)

    def test_blank_input_yields_nothing(self):
        self.assertEqual(cb.split_message("   \n  \n"), [])


class IsIgnoreMessageTests(unittest.TestCase):
    def test_ignore_alone_and_with_text(self):
        self.assertTrue(cb.is_ignore_message("/ignore"))
        self.assertTrue(cb.is_ignore_message("/ignore just a note to myself"))
        self.assertTrue(cb.is_ignore_message("/ignore\nmulti line"))

    def test_case_insensitive_and_leading_whitespace(self):
        self.assertTrue(cb.is_ignore_message("/IGNORE this"))
        self.assertTrue(cb.is_ignore_message("  /Ignore this"))

    def test_not_ignore(self):
        # A different command that merely starts with the same letters must pass.
        self.assertFalse(cb.is_ignore_message("/ignorefoo"))
        self.assertFalse(cb.is_ignore_message("please /ignore this"))
        self.assertFalse(cb.is_ignore_message("/status"))
        self.assertFalse(cb.is_ignore_message("just talking to the worker"))

    def test_empty_and_none(self):
        self.assertFalse(cb.is_ignore_message(""))
        self.assertFalse(cb.is_ignore_message(None))


class ComposerIsEmptyTests(unittest.TestCase):
    def test_empty_box(self):
        screen = "● some reply\n──── worker:foo ──\n❯ \n────\n  footer"
        self.assertTrue(cb.composer_is_empty(screen))

    def test_uses_last_prompt_not_echoed_turn(self):
        # An earlier ❯ line is an echoed conversation turn; the live box is the
        # last one and is empty here.
        screen = "❯ [Christian wrote:]\n\n hi\n● reply\n──── worker:foo ──\n❯ "
        self.assertTrue(cb.composer_is_empty(screen))

    def test_pending_text(self):
        screen = "● reply\n──── worker:foo ──\n❯ half a message"
        self.assertFalse(cb.composer_is_empty(screen))

    def test_no_prompt_at_all(self):
        self.assertFalse(cb.composer_is_empty("booting...\nno prompt yet"))
        self.assertFalse(cb.composer_is_empty(""))


class TrimUsagePanelTests(unittest.TestCase):
    PANEL = "\n".join([
        "❯ [old conversation echo]",
        "● a reply that should be trimmed off",
        "❯ /usage",
        "──── desktop / worker:welcome ──",
        "",
        "────────────────────────",
        "  Settings  Status   Config   Usage   Stats",
        "",
        "  Session",
        "  Total cost:            $1.15",
        "  Current session",
        "  ████ 17% used",
        "  Esc to cancel",
    ])

    def test_slices_from_tabbar_to_footer(self):
        out = cb.trim_usage_panel(self.PANEL)
        self.assertTrue(out.startswith("  Settings"))
        self.assertTrue(out.rstrip().endswith("Esc to cancel"))
        self.assertNotIn("a reply that should be trimmed", out)
        self.assertIn("Total cost:", out)

    def test_fallback_window_when_no_tabbar(self):
        # No tab bar → fall back to a fixed window ending at the footer, never
        # empty.
        screen = "\n".join(["line %d" % i for i in range(40)] + ["  Esc to cancel"])
        out = cb.trim_usage_panel(screen)
        self.assertTrue(out.rstrip().endswith("Esc to cancel"))
        self.assertTrue(len(out.splitlines()) > 0)

    def test_empty_input(self):
        self.assertEqual(cb.trim_usage_panel(""), "")


class ShouldResumeTests(unittest.TestCase):
    def test_matrix(self):
        self.assertTrue(cb.should_resume(True, False))
        self.assertFalse(cb.should_resume(True, True))
        self.assertFalse(cb.should_resume(False, False))
        self.assertFalse(cb.should_resume(False, True))


class ScreenIsCompactingTests(unittest.TestCase):
    def test_compacting_screen_matches(self):
        self.assertTrue(cb.screen_is_compacting("Compacting conversation…\n"))

    def test_running_turn_is_not_compaction(self):
        # A plain running turn must NOT match — steering queues fine there and
        # delaying it would defeat mid-turn check-ins.
        self.assertFalse(cb.screen_is_compacting("Working… (esc to interrupt)"))

    def test_idle_prompt_is_not_compaction(self):
        self.assertFalse(cb.screen_is_compacting("❯ "))


class ChannelFromChatTests(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(cb.channel_from_chat("discord:123"), 123)

    def test_extra_segment(self):
        self.assertEqual(cb.channel_from_chat("discord:123:456"), 123)

    def test_junk_returns_none(self):
        self.assertIsNone(cb.channel_from_chat("slack:123"))
        self.assertIsNone(cb.channel_from_chat("discord:abc"))
        self.assertIsNone(cb.channel_from_chat("discord"))
        self.assertIsNone(cb.channel_from_chat(""))
        self.assertIsNone(cb.channel_from_chat(None))


class TagInboundTests(unittest.TestCase):
    def test_typed_passes_through(self):
        self.assertEqual(cb.tag_inbound("/model opus", typed=True), "/model opus")

    def test_empty_passes_through(self):
        self.assertEqual(cb.tag_inbound("   ", typed=False), "   ")
        self.assertEqual(cb.tag_inbound("", typed=False), "")

    def test_default_tag_prefixed(self):
        out = cb.tag_inbound("hello", typed=False)
        self.assertTrue(out.startswith(cb.DISCORD_TAG))
        self.assertTrue(out.endswith("hello"))

    def test_custom_tag(self):
        out = cb.tag_inbound("hi", typed=False, tag="[SOMEONE]")
        self.assertEqual(out, "[SOMEONE]\n\nhi")


class ComposeInboundTests(unittest.TestCase):
    def test_all_parts(self):
        out = cb.compose_inbound("do the thing", ["/a/b.png"], "prior msg")
        self.assertIn("(replying to: prior msg)", out)
        self.assertIn("do the thing", out)
        self.assertIn("/a/b.png", out)
        self.assertIn("1 file", out)

    def test_text_only(self):
        self.assertEqual(cb.compose_inbound("just text"), "just text")

    def test_attachments_plural(self):
        out = cb.compose_inbound("", ["/a", "/b"])
        self.assertIn("2 files", out)
        self.assertIn("them", out)

    def test_all_empty(self):
        self.assertEqual(cb.compose_inbound("", None, None), "")


class PurgeSuffixTests(unittest.TestCase):
    def test_clean_nothing(self):
        self.assertEqual(cb.purge_suffix(0), "")

    def test_deleted_no_note_plural(self):
        self.assertEqual(cb.purge_suffix(3), " — cleared 3 messages")

    def test_deleted_no_note_singular(self):
        self.assertEqual(cb.purge_suffix(1), " — cleared 1 message")

    def test_note_with_deleted(self):
        out = cb.purge_suffix(2, "bot needs perms")
        self.assertIn("cleared 2 messages", out)
        self.assertIn("but bot needs perms", out)

    def test_note_without_deleted(self):
        out = cb.purge_suffix(0, "blocked")
        self.assertEqual(out, " — couldn't clear the channel: blocked")


class ReplyPreviewTests(unittest.TestCase):
    def test_none_when_empty(self):
        self.assertIsNone(cb.reply_preview(""))
        self.assertIsNone(cb.reply_preview("   "))
        self.assertIsNone(cb.reply_preview(None))

    def test_newlines_flattened(self):
        self.assertEqual(cb.reply_preview("a\nb\nc"), "a b c")

    def test_capped_with_ellipsis(self):
        out = cb.reply_preview("y" * 300, limit=10)
        self.assertEqual(out, "y" * 10 + "…")

    def test_under_limit_no_ellipsis(self):
        self.assertEqual(cb.reply_preview("short", limit=200), "short")


class RemoveWorkerStateTests(unittest.TestCase):
    def test_removes_existing_dir(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "worker1", "inbox"))
            self.assertTrue(cb.remove_worker_state("worker1", root))
            self.assertFalse(os.path.exists(os.path.join(root, "worker1")))

    def test_missing_dir_false(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(cb.remove_worker_state("nope", root))

    def test_traversal_refused(self):
        with tempfile.TemporaryDirectory() as root:
            outside = os.path.join(root, "outside")
            os.makedirs(outside)
            # A path-component name must be refused without touching anything.
            self.assertFalse(cb.remove_worker_state("..", root))
            self.assertFalse(cb.remove_worker_state("a/b", root))
            self.assertFalse(cb.remove_worker_state("", root))
            self.assertFalse(cb.remove_worker_state(".", root))
            self.assertTrue(os.path.isdir(outside))


class PruneOldFilesTests(unittest.TestCase):
    def test_prunes_old_keeps_new(self):
        with tempfile.TemporaryDirectory() as d:
            now = 1_000_000.0
            old = os.path.join(d, "old.bin")
            new = os.path.join(d, "new.bin")
            for p in (old, new):
                with open(p, "w") as f:
                    f.write("x")
            os.utime(old, (now - 10_000, now - 10_000))
            os.utime(new, (now - 10, now - 10))
            removed = cb.prune_old_files(d, max_age_seconds=100, now=now)
            self.assertEqual(removed, 1)
            self.assertFalse(os.path.exists(old))
            self.assertTrue(os.path.exists(new))

    def test_missing_dir_zero(self):
        self.assertEqual(
            cb.prune_old_files("/no/such/dir/xyz", 100, now=1_000_000.0), 0
        )


class ConfigTests(unittest.TestCase):
    def test_defaults_shape(self):
        cfg = cb.default_config()
        for key in ("category_id", "allowed_users", "idle_minutes",
                    "listen_port", "repos", "welcome_channel", "requests_channel"):
            self.assertIn(key, cfg)

    def test_save_then_load_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "config.json")
            cfg = cb.default_config()
            cfg["allowed_users"] = [1, 2, 3]
            cfg["repos"] = {"100": {"name": "r", "dir": "/x"}}
            cb.save_config(cfg, path)
            loaded = cb.load_config(path)
            self.assertEqual(loaded["allowed_users"], [1, 2, 3])
            self.assertEqual(loaded["repos"]["100"]["name"], "r")
            # persisted file is valid JSON
            with open(path) as f:
                self.assertEqual(json.load(f)["allowed_users"], [1, 2, 3])

    def test_load_missing_returns_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "does-not-exist.json")
            self.assertEqual(cb.load_config(path), cb.default_config())


class HarnessForTests(unittest.TestCase):
    def test_default_and_explicit(self):
        # Codex is the fleet default; an explicit harness is honored as-is.
        self.assertEqual(cb.harness_for({}), "codex")
        self.assertEqual(cb.harness_for({"harness": "claude"}), "claude")
        self.assertEqual(cb.harness_for({"harness": "codex"}), "codex")

    def test_unknown_or_empty_normalizes_to_codex(self):
        self.assertEqual(cb.harness_for({"harness": "gpt"}), "codex")
        self.assertEqual(cb.harness_for({"harness": ""}), "codex")
        self.assertEqual(cb.harness_for(None), "codex")


class ScreenIsReadyHarnessTests(unittest.TestCase):
    def test_claude_idle_and_busy(self):
        self.assertTrue(cb.screen_is_ready("some log\n❯ ", "claude"))
        self.assertFalse(cb.screen_is_ready("❯ working esc to interrupt", "claude"))

    def test_codex_prompt_char(self):
        # Codex idle uses "›"; the Claude "❯" must not read as ready for codex.
        self.assertTrue(cb.screen_is_ready("banner\n› \n gpt-5.5 · /x", "codex"))
        self.assertFalse(cb.screen_is_ready("❯ ", "codex"))

    def test_codex_busy_working_line(self):
        self.assertFalse(
            cb.screen_is_ready("› \nWorking (3s • esc to interrupt)", "codex")
        )

    def test_dismissed_trust_dialog_in_scrollback_is_ready(self):
        # A dismissed dialog lingers in scrollback (upper lines) — only the tail
        # counts, so this still reads as ready.
        screen = "Do you trust the contents of this directory?\n" + \
            "\n".join(f"line {i}" for i in range(20)) + "\n› "
        self.assertTrue(cb.screen_is_ready(screen, "codex"))

    def test_active_trust_dialog_in_tail_is_not_ready(self):
        screen = "› 1. Yes, continue\nDo you trust the contents of this directory?"
        self.assertFalse(cb.screen_is_ready(screen, "codex"))


class ComposerIsEmptyHarnessTests(unittest.TestCase):
    def test_claude_exact(self):
        self.assertTrue(cb.composer_is_empty("❯", "claude"))
        self.assertFalse(cb.composer_is_empty("❯ half-typed", "claude"))

    def test_codex_always_empty(self):
        # Codex's greyed placeholder is indistinguishable from typed text, so
        # the check conservatively returns True rather than wedge /usage.
        self.assertTrue(cb.composer_is_empty("› Run /review on my changes", "codex"))


class StartArgsHarnessTests(unittest.TestCase):
    def test_claude_injects_prompt_and_continue(self):
        args = cb.start_args("w", "/d", 42, resume=True, harness="claude")
        self.assertIn("--harness", args)
        self.assertEqual(args[args.index("--harness") + 1], "claude")
        self.assertIn("--resume", args)
        self.assertIn("--", args)
        self.assertIn("--append-system-prompt", args)

    def test_codex_no_prompt_no_profile(self):
        args = cb.start_args("w", "/d", 42, resume=True, harness="codex")
        self.assertEqual(args[args.index("--harness") + 1], "codex")
        self.assertIn("--resume", args)
        # Codex carries no settings-file profile or system-prompt injection, and
        # nothing rides after "--".
        self.assertNotIn("--", args)
        self.assertNotIn("--append-system-prompt", args)

    def test_codex_no_resume_omits_flag(self):
        args = cb.start_args("w", "/d", 42, resume=False, harness="codex")
        self.assertNotIn("--resume", args)


if __name__ == "__main__":
    unittest.main()
