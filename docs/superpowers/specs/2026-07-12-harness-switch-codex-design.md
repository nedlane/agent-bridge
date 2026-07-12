# `/harness` — switch a channel between Claude Code and Codex

**Date:** 2026-07-12
**Status:** Draft (awaiting approval)

## Goal

Add a `/harness <claude|codex> [worker]` slash command that switches a repo
channel's worker between two interactive engines:

- **claude** — today's `claude --dangerously-skip-permissions` (bypass mode).
- **codex** — OpenAI Codex in "YOLO mode"
  (`codex --dangerously-bypass-approvals-and-sandbox`), the exact analog of
  Claude's bypass-all-permissions.

Every feature the Claude worker exposes today must keep working when a channel
is switched to Codex — either identically, or with a graceful Codex-native
equivalent where the two TUIs genuinely differ.

## Key finding: the plumbing is already harness-agnostic

The Discord⇄worker contract does **not** depend on Claude:

- `discord-notify` and the relays key off the `CLAUDE_WORKER` env var and the
  worker's `meta` file (`chat=` field). Both are written by `claude-worker
  start`, so a Codex worker gets outbound Discord messaging for free.
- The bridge's event protocol (`claude.worker.turn_ended`,
  `claude.worker.send`, `claude.bridge.*`) is transport, not engine.
- All the guest/access/lockdown/addrepo/close/rename machinery is pure Discord
  bookkeeping.

What *is* Claude-coupled, and must be abstracted:

1. **Launch flags** — `claude-launch` vs a new `codex-launch`.
2. **TUI screen heuristics** — the "is it idle / busy / at a dialog" strings,
   in both `claude-worker` (bash) and `claude-bridge` (python).
3. **Turn-end relay** — Claude's `Stop` hook vs Codex's `notify` program.
4. **Reply text extraction** — Claude JSONL transcript vs Codex.
5. **In-TUI slash commands** — `/model`, `/compact`, `/usage`.

## Verified Codex facts (v0.144.1, spiked live in tmux)

| Aspect | Claude Code | Codex |
|---|---|---|
| Bypass/yolo flag | `--dangerously-skip-permissions` | `--dangerously-bypass-approvals-and-sandbox` (shows "permissions: YOLO mode") |
| Busy indicator | `esc to interrupt` | `Working (Ns • esc to interrupt)` — **contains the same `esc to interrupt` substring** |
| Idle prompt char | `❯` | `›` (plus bottom status `<model> · <dir>`) |
| Trust dialog | "Do you trust…/trust this folder" | "Do you trust the contents of this directory" — Enter accepts default "Yes" |
| Resume | `--continue` | `codex resume --last` (cwd-filtered) or `resume <id>` |
| Turn-end signal | `Stop` hook (`claude-worker-done-relay`) | `notify` program — arg JSON carries `last-assistant-message` directly |
| Reply text | parse JSONL (`type:assistant`) | `last-assistant-message` from notify payload (no parsing) |
| Instruction inject | `--append-system-prompt` | `SessionStart` hook `additionalContext` (Codex has a full hooks system) |
| Slash cmds present | `/model /compact /usage` | `/model /compact /usage /review /fast …` |
| Subscription auth | `claude-launch` strips API-billing env | `codex-launch` strips `OPENAI_API_KEY` similarly |

Crucially, **`esc to interrupt` and the trust-dialog/Enter pattern are shared**,
so most of the readiness logic converges; only the idle prompt char and the
resume mechanism truly diverge.

## Design

### 1. Data model

Each repo entry in `config.json` gains an optional `"harness"` field:

```json
"<channel_id>": { "name": "...", "dir": "...", "harness": "codex" }
```

Absent ⇒ `"claude"`, so **every existing channel is unchanged**. A tiny pure
helper `harness_for(repo)` normalizes/validates it.

### 2. `/harness` command semantics

`/harness <claude|codex> [worker]` (owner-only, via `guard()`):

1. Validate the value; set `repo["harness"]`, save config.
2. Stop the running worker (a live TUI can't hot-swap engines).
3. **Do not** resume across engines. The next channel message starts the worker
   under the new harness. Because Claude and Codex keep **independent** session
   stores, switching to Codex begins a fresh Codex session, and switching back
   to Claude resumes Claude's own last conversation. Each engine remembers its
   own thread independently — a feature, not a bug.
4. Confirm in-channel: `🔀 #repo is now driven by **codex** (YOLO mode). Your
   next message starts it.` The channel history is left intact (not purged).

`/status` gains a harness column so the current engine is always visible.

### 3. Worker launch abstraction (`claude-worker`)

`claude-worker start` gains `--harness claude|codex` (default `claude`);
recorded in `meta` as `harness=…` so `restart`, the relays, and the reaper all
know the engine without re-reading bridge config.

- **claude** path: unchanged — `claude-launch --label … -- <extra>`.
- **codex** path: a new **`bin/codex-launch`** (self-contained in this repo,
  mirroring `claude-launch`): strips `OPENAI_API_KEY`/provider env (subscription
  auth only), then `exec codex --dangerously-bypass-approvals-and-sandbox
  -C <dir> [resume args]`. The Discord protocol is injected via a Codex
  `SessionStart` hook (below), not a CLI flag.

The bash readiness helpers become harness-aware (a `HARNESS` local read from
meta):

- `is_busy` — `esc to interrupt|Compacting|Working \(` covers both engines.
- `is_trust_dialog` — add Codex's "Do you trust the contents of this directory".
- `is_ready` — accept `❯` **or** `›` as the prompt char.
- `is_resume_dialog` — Claude-only; Codex's `resume --last` doesn't prompt, so
  it simply never matches on the Codex path.
- send/paste verification — the paste-buffer + Enter + "did the box clear" loop
  is engine-neutral; the "box empty / chip present" check is made harness-aware.
  (Codex multi-line paste behavior gets one confirmation spike during build.)

### 4. Bridge screen heuristics (`claude-bridge`)

`screen_is_ready`, `screen_is_compacting`, `composer_is_empty`, and the
busy check are parametrized by harness (the bridge already knows a worker's
harness from config). They gate cold-start delivery and `/usage`. Existing
Claude behavior is the default branch, unchanged.

### 5. Turn-end relay + reply extraction

- **claude** — unchanged `Stop` hook → `claude.worker.turn_ended` with a
  transcript path; the bridge's incremental-offset parser posts the reply.
- **codex** — a new **`bin/codex-worker-done-relay`**, registered as Codex's
  `notify` program in `~/.codex/config.toml`. It reads the notify JSON arg,
  guards on `CLAUDE_WORKER` + `meta` `chat=` (non-worker Codex sessions exit 0
  silently, exactly like the Claude relay), and POSTs the **same**
  `claude.worker.turn_ended` event **with an added `reply` field** taken from
  `last-assistant-message`.

The bridge's `turn_ended` handler is extended: **when `event["reply"]` is
present, post it directly**; otherwise fall back to today's transcript parsing.
This keeps all Codex-specific parsing out of the bridge and preserves the
battle-tested Claude path untouched. Push-first (worker already spoke via
`discord-notify`) still works — it's keyed on the harness-agnostic `notified`
set.

### 6. Protocol injection (`SessionStart` hook)

A new **`bin/codex-session-protocol`** hook, registered under `[hooks]` in
`~/.codex/config.toml` for the `SessionStart` event. Guarded on `CLAUDE_WORKER`,
it emits the same `PROTOCOL` (and the orchestrator/greeter briefs where the
worker name matches) as `additionalContext`, so a Codex worker gets the exact
"reply via discord-notify" contract Claude workers get from
`--append-system-prompt`. Non-worker Codex sessions inject nothing.

### 7. In-TUI slash commands over Codex

- `/checkin`, `/clear`, `/fresh`, `/stop`, `/restart`, `/screen`, `/close`,
  `/status`, `/addrepo`, `/addguest`, `/lockdown` — **bridge-side**, already
  engine-neutral once §3–§4 land. `/clear` and `/fresh` (deterministic
  stop + fresh start) work identically.
- `/model <m>` — typed into the TUI. Codex `/model` opens a picker rather than
  taking an inline arg; the bridge will type `/model` then the value and submit,
  or fall back to `-c model=…` on restart. Confirmed during build.
- `/compact [focus]` — Codex has `/compact`; the focus arg may be dropped.
- `/usage` — Codex also has a `/usage` panel. The screenshot flow (open panel,
  capture, Escape to dismiss) is reused; `trim_usage_panel`'s marker strings
  ("Settings…Usage…Stats" / "Esc to cancel") are made harness-aware, degrading
  to a full-panel screenshot if markers aren't found (as it already does).
- Arbitrary `/anything` passthrough — still typed verbatim, so Codex's own
  slash commands work.

### 8. Registration & install

`scripts/link.sh` symlinks the two new `bin/` scripts. A short setup step (or a
`bridge-ctl`/docs note) adds the Codex `notify` + `SessionStart` lines to
`~/.codex/config.toml`, mirroring how `~/.claude/settings.json` registers the
Claude relays. Idempotent.

## Testing

- **Unit (stdlib, tests/):** `harness_for()` normalization/validation; the
  harness-aware `screen_is_ready`/`screen_is_compacting`/`composer_is_empty`
  branches for Codex strings; the `turn_ended` handler posting `event["reply"]`
  when present; config round-trip with the new field.
- **Relay:** `codex-worker-done-relay` builds the correct signed body from a
  sample notify payload and exits 0 (silent) for a non-worker session.
- **Live spike:** start a Codex worker via `claude-worker start --harness
  codex`, confirm boot→idle detection, a relayed message round-trips a reply to
  Discord, `/screen` and `/usage` render, and `/harness claude` switches back
  and resumes the Claude thread.

## Out of scope / YAGNI

- No per-message engine switching; harness is per-channel.
- No cross-engine conversation migration (each keeps its own history).
- No Codex `PostToolUse` todo relay initially (Claude's todo relay stays; a
  Codex equivalent can follow if wanted).
- No new access-profile semantics; Codex YOLO == Claude bypass (owner/collab
  full-trust only, same as today).

## Open questions for Ned

1. **Switch semantics** — OK that switching engines keeps each engine's *own*
   last conversation (Codex fresh first time; Claude resumes its thread), rather
   than always-fresh or wiping the channel?
2. **Feature parity bar** — "works in Codex" as *functionally equivalent,
   degrading gracefully where the TUIs differ* (e.g. `/usage` is Codex's own
   panel, `/model` is its picker) — acceptable, or must anything be identical?
3. **Anything to explicitly exclude** from the Codex path?

## Revisions after the implementation spike

Two mechanisms changed once tested live against Codex 0.144.1:

- **Turn-end signal — `notify` → Stop hook.** Codex's `notify` program fires
  only in `codex exec` (non-interactive), *not* in the interactive TUI the
  workers run. Codex's **`Stop` hook** does fire in the TUI, and its payload
  carries `last_assistant_message`, `transcript_path`, `session_id`, and
  `stop_hook_active` — a near-exact match for Claude's Stop hook. So
  `codex-worker-done-relay` is registered as a **Stop hook**, not a notify
  program. To avoid a "Hooks need review" prompt on Ned's ordinary `codex`
  sessions, the hook lives in a **worker profile** (`~/.codex/worker.config.toml`)
  that only workers load via `codex-launch -p worker`
  (+ `--dangerously-bypass-hook-trust`); the base config is untouched.

- **Protocol injection — SessionStart hook → first message.** A Codex
  `SessionStart` hook fires, but its `additionalContext` is only *displayed* in
  the TUI; it does not reliably reach the model's context (verified: the model
  couldn't recall an injected secret). So the protocol is injected the
  deterministic way — the bridge **prepends it to the worker's first message**
  on a fresh Codex session (tracked by `codex_unprimed`), and the per-message
  `DISCORD_TAG` keeps the "reply via discord-notify" contract in front of the
  worker every turn thereafter.

Everything else landed as designed. The full chain (start → ready-detection →
multi-line send → Stop-hook relay with reply → resume) was validated live in an
isolated `CODEX_HOME`.
