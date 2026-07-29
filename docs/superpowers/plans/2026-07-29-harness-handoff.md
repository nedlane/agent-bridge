# /harness handoff argument Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in `handoff` argument to `/harness` so the outgoing engine can leave a short context note that primes whichever engine starts next.

**Architecture:** Everything lives in the single existing `bin/agent-bridge` module (no new files — this codebase keeps the whole daemon in one file, tests in one `tests/test_claude_bridge.py`). Two new stdlib-only helpers (`handoff_path`, `consume_handoff`) follow the exact `fresh_marker_path`/`prune_old_files` pattern already in the file. `do_harness` gains a capture step that asks the live worker to write the note and waits for it; `system_prompt` (Claude) and the Codex first-message composer in `handle_repo_message` each get a one-line consumption hook, mirroring how the existing Discord-protocol injection is already split across those same two points.

**Tech Stack:** Python 3 stdlib only for the pure helpers (testable without `discord.py`); `unittest` for tests.

## Global Constraints

- Shell: n/a — no shell files touched.
- Python: module-level code in `bin/agent-bridge` must stay stdlib-only (no `discord.py`/`aiohttp` imports outside `run_bridge()`), so the pure helpers remain importable by `tests/test_claude_bridge.py` without those packages installed.
- Per `CONTRIBUTING.md`: any slash-command surface change updates the module docstring and the README (this PR does not touch `skills/claude-bridge/SKILL.md` — grep confirms it does not currently document `/harness` at all, a pre-existing gap out of scope here).
- Reproduce CI locally before the final commit: `python3 -m py_compile bin/agent-bridge` and `python3 -m unittest discover -s tests`.
- Spec: `docs/superpowers/specs/2026-07-29-harness-handoff-design.md` — every task below implements one piece of it; do not deviate from its storage location (bridge state dir, transient), timeout behavior (degrade gracefully), or injection points without checking back against it.

---

### Task 1: `handoff_path` / `consume_handoff` helpers

**Files:**
- Modify: `bin/agent-bridge:112` (new constant, right after `INBOX_MAX_AGE_SECONDS`)
- Modify: `bin/agent-bridge:860-861` (new functions, right after `prune_old_files`, before the `# --- Runtime` section comment)
- Test: `tests/test_claude_bridge.py` (new test classes, appended before the `if __name__ == "__main__":` block)

**Interfaces:**
- Produces: `HANDOFF_MAX_AGE_SECONDS` (int, seconds). `handoff_path(name, state_root=None) -> str`. `consume_handoff(name, state_root=None, now=None) -> str | None` — read-and-delete once; discards (and returns `None` for) a file older than `HANDOFF_MAX_AGE_SECONDS`; returns `None` if absent or empty.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_claude_bridge.py`, right before the final `if __name__ == "__main__":` line:

```python
class HandoffPathTests(unittest.TestCase):
    def test_path_shape(self):
        self.assertEqual(
            cb.handoff_path("myworker", "/state"),
            "/state/myworker/handoff.md",
        )


class ConsumeHandoffTests(unittest.TestCase):
    def test_absent_returns_none(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(cb.consume_handoff("w", root))

    def test_reads_and_deletes_once(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "w"))
            path = cb.handoff_path("w", root)
            with open(path, "w") as f:
                f.write("  finish the auth refactor  \n")
            self.assertEqual(cb.consume_handoff("w", root), "finish the auth refactor")
            self.assertFalse(os.path.exists(path))
            # one-shot: a second read finds nothing
            self.assertIsNone(cb.consume_handoff("w", root))

    def test_empty_file_returns_none(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "w"))
            open(cb.handoff_path("w", root), "w").close()
            self.assertIsNone(cb.consume_handoff("w", root))

    def test_stale_file_discarded_unread(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "w"))
            path = cb.handoff_path("w", root)
            with open(path, "w") as f:
                f.write("old context")
            now = 1_000_000.0
            os.utime(path, (now - cb.HANDOFF_MAX_AGE_SECONDS - 10,) * 2)
            self.assertIsNone(cb.consume_handoff("w", root, now=now))
            self.assertFalse(os.path.exists(path))

    def test_fresh_file_within_age_kept(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "w"))
            path = cb.handoff_path("w", root)
            with open(path, "w") as f:
                f.write("recent context")
            now = 1_000_000.0
            os.utime(path, (now - 10,) * 2)
            self.assertEqual(cb.consume_handoff("w", root, now=now), "recent context")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/agent-bridge && python3 -m unittest tests.test_claude_bridge.HandoffPathTests tests.test_claude_bridge.ConsumeHandoffTests -v`
Expected: FAIL / ERROR — `AttributeError: module 'claude_bridge' has no attribute 'handoff_path'` (and `HANDOFF_MAX_AGE_SECONDS`, `consume_handoff`).

- [ ] **Step 3: Implement**

In `bin/agent-bridge`, right after the `INBOX_MAX_AGE_SECONDS = 7 * 24 * 3600` line (currently line 112):

```python
# A /harness handoff note nobody's switch ever consumed (e.g. a second
# switch arrived before any message reached the new engine) is discarded
# unread after this long, rather than surfacing in an unrelated later
# session.
HANDOFF_MAX_AGE_SECONDS = 24 * 3600
```

Right after `prune_old_files` (ends at current line 860, just before the
`# --- Runtime ...` section comment at line 863):

```python
def handoff_path(name, state_root=None):
    return os.path.join(state_root or STATE_ROOT, name, "handoff.md")


def consume_handoff(name, state_root=None, now=None):
    """Read-and-delete a pending /harness handoff note once: returns its
    content, or None if absent/empty. A note older than
    HANDOFF_MAX_AGE_SECONDS is discarded unread — an unconsumed handoff from
    a switch nobody followed up on shouldn't surface in an unrelated later
    session."""
    path = handoff_path(name, state_root)
    now = time.time() if now is None else now
    try:
        stale = now - os.path.getmtime(path) > HANDOFF_MAX_AGE_SECONDS
    except OSError:
        return None
    if stale:
        try:
            os.remove(path)
        except OSError:
            pass
        return None
    try:
        with open(path) as f:
            content = f.read().strip()
        os.remove(path)
    except OSError:
        return None
    return content or None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/agent-bridge && python3 -m unittest tests.test_claude_bridge.HandoffPathTests tests.test_claude_bridge.ConsumeHandoffTests -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
cd ~/agent-bridge
git add bin/agent-bridge tests/test_claude_bridge.py
git commit -m "handoff: add handoff_path/consume_handoff state helpers"
```

---

### Task 2: Inject a pending handoff into `system_prompt` (Claude side)

**Files:**
- Modify: `bin/agent-bridge:213-220` (`system_prompt`)
- Test: `tests/test_claude_bridge.py`

**Interfaces:**
- Consumes: `consume_handoff(name, state_root=None)` from Task 1.
- Produces: `system_prompt(name, state_root=None) -> str` (new optional second param; existing call site `start_args`'s `system_prompt(name)` keeps working unchanged since it's optional).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_claude_bridge.py`:

```python
class SystemPromptHandoffTests(unittest.TestCase):
    def test_no_handoff_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(cb.system_prompt("w", root), cb.PROTOCOL)

    def test_appends_and_consumes_pending_handoff(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "w"))
            with open(cb.handoff_path("w", root), "w") as f:
                f.write("finish the auth refactor")
            prompt = cb.system_prompt("w", root)
            self.assertTrue(prompt.startswith(cb.PROTOCOL))
            self.assertIn("finish the auth refactor", prompt)
            # one-shot: consumed by the call above
            self.assertEqual(cb.system_prompt("w", root), cb.PROTOCOL)

    def test_welcome_and_orchestrator_bases_unaffected(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(cb.system_prompt("welcome", root), cb.GREETER)
            self.assertEqual(
                cb.system_prompt("orchestrator", root),
                cb.PROTOCOL + "\n\n" + cb.ORCHESTRATOR,
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/agent-bridge && python3 -m unittest tests.test_claude_bridge.SystemPromptHandoffTests -v`
Expected: FAIL — `TypeError: system_prompt() takes 1 positional argument but 2 were given`

- [ ] **Step 3: Implement**

Replace `system_prompt` (current lines 213-220):

```python
def system_prompt(name):
    """The injected protocol; the orchestrator also gets the control-plane
    brief, and the public welcome greeter gets its own brief instead."""
    if name == "welcome":
        return GREETER
    if name == "orchestrator":
        return PROTOCOL + "\n\n" + ORCHESTRATOR
    return PROTOCOL
```

with:

```python
def system_prompt(name, state_root=None):
    """The injected protocol; the orchestrator also gets the control-plane
    brief, and the public welcome greeter gets its own brief instead. A
    pending /harness handoff note is appended and consumed, one-shot."""
    if name == "welcome":
        base = GREETER
    elif name == "orchestrator":
        base = PROTOCOL + "\n\n" + ORCHESTRATOR
    else:
        base = PROTOCOL
    handoff = consume_handoff(name, state_root)
    if handoff:
        base += "\n\n---\n\nHandoff from the previous engine:\n\n" + handoff
    return base
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/agent-bridge && python3 -m unittest tests.test_claude_bridge.SystemPromptHandoffTests -v`
Expected: PASS (3 tests)

Also run the full suite to confirm nothing else broke (the `StartArgsHarnessTests` call `start_args`, which calls `system_prompt(name)` with the now-optional second arg defaulted):

Run: `cd ~/agent-bridge && python3 -m unittest discover -s tests -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
cd ~/agent-bridge
git add bin/agent-bridge tests/test_claude_bridge.py
git commit -m "handoff: inject a pending handoff into system_prompt for Claude"
```

---

### Task 3: Capture the handoff in `do_harness` + `/harness` command argument

**Files:**
- Modify: `bin/agent-bridge:207-211` (new constants, right after `DISCORD_TAG`)
- Modify: `bin/agent-bridge:1366-1407` (`do_harness`)
- Modify: `bin/agent-bridge:1762-1768` (`slash_harness`)

**Interfaces:**
- Consumes: `handoff_path(name)` (Task 1), `deliver(name, text, message=None, typed=False, tag=None)`, `wait_ready(name, timeout=180)` (both pre-existing closures in `run_bridge`).
- Produces: `do_harness(name, engine, handoff=False)` (new third param, default preserves old behavior exactly). `slash_harness` gains a `handoff: bool = False` Discord option.

No unit tests here: `do_harness`/`slash_harness` are closures defined inside `run_bridge()`, which (per the module's own doc comment and every existing test) is exercised live, not under `unittest` — `discord.py`/`aiohttp` aren't installed in the test environment. Verify with `py_compile` (syntax) in this task, and the full suite in Task 5 (regression check on the pure-helper tests this doesn't touch).

- [ ] **Step 1: Add the prompt/timeout constants**

Right after `DISCORD_TAG = (...)` (current lines 207-211) in `bin/agent-bridge`:

```python
# /harness ... handoff:True sends this to the outgoing worker before
# stopping it, then waits this long for it to go idle again before
# switching anyway (see capture_handoff in run_bridge).
HANDOFF_TIMEOUT_SECONDS = 240

HANDOFF_PROMPT = (
    "You're about to be switched to a different engine that keeps a "
    "completely separate conversation — it will remember nothing from this "
    "session. Before that happens, write a concise handoff (current task, "
    "key decisions so far, files touched, next steps) to exactly this "
    "path: {path}\nKeep it under ~400 words."
)
```

- [ ] **Step 2: Add `capture_handoff` and wire it into `do_harness`**

Replace the current `do_harness` (lines 1366-1407):

```python
    async def do_harness(name, engine):
        """Switch a channel's worker between engines ('claude' or 'codex').

        Not a hot-swap — a live TUI can't change engines — so this persists the
        choice, stops the running worker, and lets the next message start it on
        the new engine. Claude and Codex keep SEPARATE session stores, so
        switching to Codex begins a fresh Codex session while Claude's thread
        stays intact (and vice versa): each engine remembers its own last
        conversation. Codex always runs in YOLO mode (bypass all approvals +
        sandbox), the analog of Claude's bypass-permissions."""
        engine = (engine or "").strip().lower()
        if engine not in ("claude", "codex"):
            return f"⚠️ harness must be `claude` or `codex` (got `{engine}`)", None
        item = next(
            ((cid, r) for cid, r in cfg["repos"].items() if r["name"] == name), None
        )
        if not item:
            return f"⚠️ no repo channel mapped for `{name}`", None
        _, repo = item
        if engine == "codex" and repo.get("profile") in RESTRICTED_PROFILES:
            return (
                f"🚫 `{name}` runs the restricted **{repo['profile']}** profile, "
                "which only Claude can enforce — Codex runs full-YOLO and would "
                "ignore its guardrails. Keeping it on Claude.", None,
            )
        if harness_for(repo) == engine:
            return f"↔️ `{name}` is already on **{engine}** — nothing to change.", None
        repo["harness"] = engine
        save_config(cfg, config_path)
        # Stop the current worker so the next message revives it on the new
        # engine; a stale codex-prime flag from the old engine is cleared.
        if await asyncio.to_thread(worker_alive, name):
            await asyncio.to_thread(worker_stop, name)
        codex_unprimed.discard(name)
        last_activity.pop(name, None)
        note = " **(YOLO mode)**" if engine == "codex" else ""
        print(f"[bridge] harness: {name} -> {engine}")
        return (
            f"🔀 `{name}` is now driven by **{engine}**{note}. Your next message "
            "starts it — each engine keeps its own conversation, so switching "
            "back resumes that engine's last thread.", None,
        )
```

with:

```python
    async def capture_handoff(name):
        """Ask the live worker to write a handoff note for the next engine,
        wait for it to finish, and return a short status suffix. Never
        blocks the switch — always returns, whatever happened."""
        try:
            os.remove(handoff_path(name))
        except FileNotFoundError:
            pass
        await deliver(name, HANDOFF_PROMPT.format(path=handoff_path(name)))
        if not await wait_ready(name, timeout=HANDOFF_TIMEOUT_SECONDS):
            return " ⚠️ handoff didn't finish in time — switching without it."
        if os.path.isfile(handoff_path(name)):
            return " 📝 handoff captured for the next engine."
        return " ⚠️ didn't write a handoff file — switching without one."

    async def do_harness(name, engine, handoff=False):
        """Switch a channel's worker between engines ('claude' or 'codex').

        Not a hot-swap — a live TUI can't change engines — so this persists the
        choice, stops the running worker, and lets the next message start it on
        the new engine. Claude and Codex keep SEPARATE session stores, so
        switching to Codex begins a fresh Codex session while Claude's thread
        stays intact (and vice versa): each engine remembers its own last
        conversation. Codex always runs in YOLO mode (bypass all approvals +
        sandbox), the analog of Claude's bypass-permissions.

        handoff=True asks the outgoing worker to write a short context note
        for whichever engine starts next before it's stopped (see
        capture_handoff) — best-effort, the switch proceeds either way."""
        engine = (engine or "").strip().lower()
        if engine not in ("claude", "codex"):
            return f"⚠️ harness must be `claude` or `codex` (got `{engine}`)", None
        item = next(
            ((cid, r) for cid, r in cfg["repos"].items() if r["name"] == name), None
        )
        if not item:
            return f"⚠️ no repo channel mapped for `{name}`", None
        _, repo = item
        if engine == "codex" and repo.get("profile") in RESTRICTED_PROFILES:
            return (
                f"🚫 `{name}` runs the restricted **{repo['profile']}** profile, "
                "which only Claude can enforce — Codex runs full-YOLO and would "
                "ignore its guardrails. Keeping it on Claude.", None,
            )
        if harness_for(repo) == engine:
            return f"↔️ `{name}` is already on **{engine}** — nothing to change.", None
        repo["harness"] = engine
        save_config(cfg, config_path)
        # Stop the current worker so the next message revives it on the new
        # engine; a stale codex-prime flag from the old engine is cleared.
        handoff_note = ""
        if await asyncio.to_thread(worker_alive, name):
            if handoff:
                handoff_note = await capture_handoff(name)
            await asyncio.to_thread(worker_stop, name)
        elif handoff:
            handoff_note = " ℹ️ no running session to capture a handoff from."
        codex_unprimed.discard(name)
        last_activity.pop(name, None)
        note = " **(YOLO mode)**" if engine == "codex" else ""
        print(f"[bridge] harness: {name} -> {engine}")
        return (
            f"🔀 `{name}` is now driven by **{engine}**{note}.{handoff_note} Your "
            "next message starts it — each engine keeps its own conversation, "
            "so switching back resumes that engine's last thread.", None,
        )
```

- [ ] **Step 3: Add the `handoff` option to `/harness`**

Replace the current `slash_harness` (lines 1762-1768):

```python
    @tree.command(name="harness", description="Switch a channel between Claude and Codex (codex = YOLO mode)")
    @describe(engine="claude or codex", worker="Worker (defaults to this channel's worker)")
    async def slash_harness(interaction, engine: str, worker: str = None):
        if not (name := await guard(interaction, worker)):
            return
        await interaction.response.defer(thinking=True)
        await reply(interaction, *await do_harness(name, engine))
```

with:

```python
    @tree.command(name="harness", description="Switch a channel between Claude and Codex (codex = YOLO mode)")
    @describe(engine="claude or codex", worker="Worker (defaults to this channel's worker)",
              handoff="Ask the outgoing worker to write a handoff note for the next engine first (default: off)")
    async def slash_harness(interaction, engine: str, worker: str = None, handoff: bool = False):
        if not (name := await guard(interaction, worker)):
            return
        await interaction.response.defer(thinking=True)
        await reply(interaction, *await do_harness(name, engine, handoff))
```

- [ ] **Step 4: Verify syntax**

Run: `cd ~/agent-bridge && python3 -m py_compile bin/agent-bridge`
Expected: no output, exit code 0

- [ ] **Step 5: Commit**

```bash
cd ~/agent-bridge
git add bin/agent-bridge
git commit -m "handoff: capture a handoff note in do_harness, add /harness handoff arg"
```

---

### Task 4: Deliver the handoff on the Codex side

**Files:**
- Modify: `bin/agent-bridge:1271-1278` (`handle_repo_message`, the composed-message block)

**Interfaces:**
- Consumes: `consume_handoff(name)` (Task 1).

No unit test — same closure-testability note as Task 3. Verified with `py_compile` here; full regression suite in Task 5.

- [ ] **Step 1: Implement**

Replace the current block (lines 1271-1278):

```python
        composed = compose_inbound(message.content, paths, quote)
        # First real message to a fresh Codex worker: prepend the Discord
        # protocol (Codex's stand-in for Claude's --append-system-prompt). One
        # shot — a resumed session already has it in history.
        if name in codex_unprimed:
            composed = system_prompt(name) + "\n\n---\n\n" + composed
            codex_unprimed.discard(name)
        await deliver(name, composed, message, typed=False)
```

with:

```python
        composed = compose_inbound(message.content, paths, quote)
        # A pending /harness handoff rides on the first real message to
        # whichever engine starts next — including a Codex worker resuming
        # its OWN prior thread here, since the note is catching it up on what
        # the OTHER engine just did, not on its own history (so this is
        # deliberately not gated on codex_unprimed below).
        handoff = consume_handoff(name)
        if handoff:
            composed = (
                "Handoff from the previous engine:\n\n" + handoff + "\n\n---\n\n" + composed
            )
        # First real message to a fresh Codex worker: prepend the Discord
        # protocol (Codex's stand-in for Claude's --append-system-prompt). One
        # shot — a resumed session already has it in history.
        if name in codex_unprimed:
            composed = system_prompt(name) + "\n\n---\n\n" + composed
            codex_unprimed.discard(name)
        await deliver(name, composed, message, typed=False)
```

- [ ] **Step 2: Verify syntax**

Run: `cd ~/agent-bridge && python3 -m py_compile bin/agent-bridge`
Expected: no output, exit code 0

- [ ] **Step 3: Commit**

```bash
cd ~/agent-bridge
git add bin/agent-bridge
git commit -m "handoff: deliver a pending handoff on the Codex first-message path"
```

---

### Task 5: Docs sync + final verification

**Files:**
- Modify: `bin/agent-bridge:35` (module docstring)
- Modify: `README.md:273`

**Interfaces:** none (docs only).

- [ ] **Step 1: Update the module docstring**

In `bin/agent-bridge`, replace line 35:

```
  /harness <claude|codex> [worker]   switch a channel's engine (codex = YOLO)
```

with:

```
  /harness <claude|codex> [worker] [handoff]   switch a channel's engine
                           (codex = YOLO; handoff first captures a note for
                           the next engine, best-effort)
```

- [ ] **Step 2: Update the README command table**

In `README.md`, replace line 273:

```
| `/harness <claude\|codex> [worker]` | Switch a channel between **Claude Code** and **Codex** (Codex runs in YOLO mode — bypass all approvals + sandbox, the analog of Claude's bypass-permissions). Stops the worker; the next message starts it on the new engine. Each engine keeps its **own** conversation, so switching back resumes that engine's last thread. |
```

with:

```
| `/harness <claude\|codex> [worker] [handoff]` | Switch a channel between **Claude Code** and **Codex** (Codex runs in YOLO mode — bypass all approvals + sandbox, the analog of Claude's bypass-permissions). Stops the worker; the next message starts it on the new engine. Each engine keeps its **own** conversation, so switching back resumes that engine's last thread. `handoff:True` first asks the outgoing worker to write a short handoff note (current task, decisions, next steps) that's injected into the new engine's first turn — best-effort; the switch proceeds either way. |
```

- [ ] **Step 3: Full local CI reproduction**

Run:
```bash
cd ~/agent-bridge
python3 -m py_compile bin/agent-bridge
python3 -m unittest discover -s tests -v
```
Expected: `py_compile` silent/exit 0; every test in `tests/test_claude_bridge.py` PASSES, including the new `HandoffPathTests`, `ConsumeHandoffTests`, and `SystemPromptHandoffTests` classes from Tasks 1-2 and every pre-existing test (no regressions).

- [ ] **Step 4: Commit**

```bash
cd ~/agent-bridge
git add bin/agent-bridge README.md
git commit -m "handoff: document the /harness handoff argument"
```

- [ ] **Step 5: Push and open the PR**

No push access to `nedlane/agent-bridge` — `gh pr create` auto-forks under the account's own namespace, pushes the branch there, and opens the PR against upstream `main`:

```bash
cd ~/agent-bridge
gh pr create --repo nedlane/agent-bridge --base main --head harness-handoff \
  --title "Add handoff argument to /harness" \
  --body "$(cat <<'EOF'
## Summary

Adds an opt-in `handoff` argument to `/harness` so the outgoing engine can
leave a short context note that primes whichever engine starts next, closing
the "no cross-engine conversation migration" gap the original harness-switch
design (docs/superpowers/specs/2026-07-12-harness-switch-codex-design.md)
explicitly left out of scope.

Design: docs/superpowers/specs/2026-07-29-harness-handoff-design.md

## What changed

- `handoff_path`/`consume_handoff`: transient state-dir marker file
  (`~/.local/state/claude-workers/<name>/handoff.md`), read-and-delete once,
  with a 24h staleness cap (mirrors the existing inbox-pruning pattern).
- `do_harness` gains a `handoff=False` param: when true and the outgoing
  worker is alive, it's asked to write the note, the switch waits (bounded,
  240s) for it to go idle, then proceeds either way — best-effort, never
  blocks the switch.
- `system_prompt` (Claude) and the Codex first-message composer in
  `handle_repo_message` each consume a pending handoff at their existing
  one-shot injection points.
- `/harness` gains a `handoff: bool = False` Discord option.
- Module docstring + README updated.

## How tested

- [x] `python3 -m py_compile bin/agent-bridge`
- [x] `python3 -m unittest discover -s tests`
- [ ] `bash -n` + `shellcheck` — n/a, no shell files touched
- [ ] `jq empty claude-profiles/*.json` — n/a, no profile JSON touched

## Checklist

- [x] `py_compile` passes on `bin/agent-bridge`
- [x] Unit tests pass
- [x] Module docstring and README updated (this repo's `claude-bridge` skill
      does not currently document `/harness` at all — pre-existing gap, not
      touched here)
EOF
)"
```

Report the returned PR URL back to the user.
