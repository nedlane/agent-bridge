# `/harness ... handoff:True` — carry context across an engine switch

**Date:** 2026-07-29
**Status:** Draft (awaiting approval)

## Goal

`/harness` (see `docs/superpowers/specs/2026-07-12-harness-switch-codex-design.md`)
switches a channel's worker between Claude Code and Codex. By design, the two
engines keep **separate** conversation histories — switching to an engine that
hasn't run in this channel before starts it with zero memory of what the other
engine was just doing. That was explicitly out of scope for the original
design ("No cross-engine conversation migration").

This adds an opt-in `handoff` argument to `/harness` that closes that gap: the
outgoing worker is asked to write a short handoff note before it's stopped,
and the new engine's very first turn is primed with it.

## Non-goals

- Not a general summarization/compaction feature — `/compact` already exists
  for that, within one engine.
- Not persistent or human-facing. The handoff exists only to bridge one
  switch; it is not a project changelog and isn't meant to be read outside
  the two workers involved.
- No custom per-switch prompt/focus argument (cf. `/compact`'s `focus`) — YAGNI
  until someone needs it.

## Design

### 1. Command surface

`/harness <claude|codex> [worker] [handoff]`:

```
@tree.command(name="harness", ...)
@describe(engine="claude or codex", worker="Worker (defaults to this channel's worker)",
          handoff="Ask the outgoing worker to write a handoff note for the next engine first (default: off)")
async def slash_harness(interaction, engine: str, worker: str = None, handoff: bool = False):
```

Discord renders `handoff` as a boolean toggle. Default `False` preserves
today's behavior exactly.

### 2. Capture (outgoing engine)

In `do_harness`, when `handoff=True` and the current worker is alive, **before**
the existing stop-and-persist logic:

1. Remove any stale `handoff_path(name)` file (ignore `FileNotFoundError`), so
   the post-check below unambiguously reflects this attempt, not a leftover.
2. `deliver(name, HANDOFF_PROMPT)` — a fixed instruction (not typed, so it
   reads as a normal steering message, queued like any other mid-turn input):

   > "You're about to be switched to a different engine that keeps a
   > completely separate conversation — it will remember nothing from this
   > session. Before that happens, write a concise handoff (current task,
   > key decisions so far, files touched, next steps) to exactly this path:
   > `<handoff_path>`. Keep it under ~400 words."

3. `wait_ready(name, timeout=HANDOFF_TIMEOUT_SECONDS)` — reuses the existing
   idle-poll helper (already used after resume/restart). `HANDOFF_TIMEOUT_SECONDS
   = 240`, a bit more headroom than `wait_ready`'s own 180s default since this
   trails whatever the worker was already doing.
4. Check `os.path.isfile(handoff_path(name))` and fold the outcome into the
   existing switch-confirmation reply:
   - captured → `📝 handoff captured for the next engine.`
   - turn finished, no file → `⚠️ didn't write a handoff file — switching without one.`
   - timed out → `⚠️ handoff didn't finish in time — switching without it.`

If the worker isn't alive when `handoff=True` is requested, skip straight to
the normal switch with a note (`ℹ️ no running session to capture a handoff
from`) — there's nothing to ask.

**Per the "degrade gracefully" decision: the switch always proceeds**,
whether or not the handoff was captured. This is not a blocking gate.

### 3. Storage

```python
def handoff_path(name, state_root=None):
    return os.path.join(state_root or STATE_ROOT, name, "handoff.md")
```

Mirrors the existing `fresh_marker_path` — same per-worker directory under
`~/.local/state/claude-workers/<name>/` that already holds `meta` and
`no-resume`. Nothing is written into the target repo; nothing to `.gitignore`.

```python
HANDOFF_MAX_AGE_SECONDS = 86400  # mirrors INBOX_MAX_AGE_SECONDS's staleness cap

def consume_handoff(name, state_root=None):
    """Read-and-delete: returns the handoff content once, or None. A file
    older than HANDOFF_MAX_AGE_SECONDS is discarded unread — an unconsumed
    handoff from a switch nobody followed up on shouldn't surface in an
    unrelated later session."""
    path = handoff_path(name, state_root)
    try:
        if time.time() - os.path.getmtime(path) > HANDOFF_MAX_AGE_SECONDS:
            os.remove(path)
            return None
        with open(path) as f:
            content = f.read().strip()
        os.remove(path)
        return content or None
    except OSError:
        return None
```

### 4. Delivery (incoming engine)

Both engines already have an injection point for one-shot "prime the next
session" content — the handoff reuses each, rather than adding new plumbing:

- **Claude** — `system_prompt(name)` is called fresh on every Claude start
  (baked into `--append-system-prompt` via `start_args`). It calls
  `consume_handoff(name)` and, if present, appends it:

  ```python
  def system_prompt(name):
      base = ...  # existing GREETER / PROTOCOL+ORCHESTRATOR / PROTOCOL logic
      handoff = consume_handoff(name)
      if handoff:
          base += "\n\n---\n\nHandoff from the previous engine:\n\n" + handoff
      return base
  ```

  Runs on every Claude start; a no-op (one `isfile`-equivalent stat via
  `getmtime`) when no handoff is pending, so this is safe to leave unconditional.

- **Codex** — consumed at the exact call site that already prepends
  `system_prompt(name)` for `codex_unprimed` workers, in `handle_repo_message`:

  ```python
  composed = compose_inbound(message.content, paths, quote)
  handoff = consume_handoff(name)
  if handoff:
      composed = "Handoff from the previous engine:\n\n" + handoff + "\n\n---\n\n" + composed
  if name in codex_unprimed:
      composed = system_prompt(name) + "\n\n---\n\n" + composed
      codex_unprimed.discard(name)
  await deliver(name, composed, message, typed=False)
  ```

  Deliberately **not** gated on `codex_unprimed`: a handoff is still relevant
  even when Codex is resuming its own prior thread in this channel, since it's
  catching up on what the *other* engine just did, not on its own history.
  Only fires on the first real (non-slash-command) message, same as the
  existing protocol-priming — consistent with how that one-shot injection
  already behaves.

Claude's consumption happens at worker start (inside `start_args`, before any
message); Codex's happens later, at first-message time, because that's also
when Codex's own protocol injection happens (`start_args` returns before
calling `system_prompt` on the Codex path). No new in-memory tracking set is
needed on either side — `consume_handoff`'s read-and-delete is the only state.

### 5. Edge cases

- **Switch again before the pending handoff is ever consumed** (e.g. codex→claude
  with a handoff, then claude→codex again before any message reaches the
  claude worker): the file is simply overwritten by the second capture if
  `handoff=True` again, or left for whichever engine starts first to consume —
  last-written-wins. Rare enough not to special-case.
- **Restricted profiles** (`greeter`/`utility`) — `/harness` is owner-only
  already (`guard`, not `guard_scoped`), and `harness_for` forces those
  profiles to `claude` regardless of config, so this path is not reachable
  for them in practice.
- **Bridge restart between capture and consumption** — the file survives on
  disk, so a `systemctl --user restart claude-bridge` between the switch and
  the next message doesn't lose it.

## Testing

Same shape as the original harness design's test plan:

- **Unit (stdlib, `tests/`):** `handoff_path` construction; `consume_handoff`
  round-trip (write → consume → gone, consume-when-absent → `None`,
  consume-when-stale → discarded and `None`); `system_prompt` appends handoff
  content when present.
- **Live spike:** `/harness codex handoff:True` on a running Claude worker,
  confirm it writes `handoff.md`, the switch completes, and the next message
  to the Codex worker is primed with the handoff text ahead of the protocol
  and the user's message. Repeat the reverse direction (codex → claude).
  Confirm the timeout path (stop the worker mid-write, or shrink
  `HANDOFF_TIMEOUT_SECONDS` for the spike) still completes the switch with the
  "didn't finish in time" note.

## Docs to keep in sync (per `CONTRIBUTING.md`)

- `/harness` line in the `bin/agent-bridge` module docstring.
- The `/harness` row in `README.md`'s command table.
- `skills/claude-bridge/SKILL.md`, if/where it documents `/harness`.

## Out of scope / YAGNI

- No custom per-switch handoff prompt/focus argument.
- No handoff support for `/clear`/`/fresh`/`/restart` (same-engine, no context
  gap to bridge) — scoped to `/harness` only, as requested.
- No `/status` column for "handoff pending" — the confirmation message at
  switch time is the only surfaced signal.
