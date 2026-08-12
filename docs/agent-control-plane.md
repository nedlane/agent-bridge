# The local agent control plane

This document describes how a Discord message becomes work done by an
interactive Claude Code session on this machine, and — most importantly — the
**Claude Code hook setup** the tools in this repo point back here for.

## 1. Overview / data flow

```
Discord channel  (one channel per repo, under a "Claude" category)
      │
      ▼
agent-bridge           discord.py client + signed HTTP listener (+ /healthz)
                       local by default; tailnet-bindable for satellites
      │  forwards locally, or over SSH to a configured worker host
      ▼
agent-worker          lifecycle over a tmux session named `cw-<name>`
      ├── Claude/legacy Codex TUI: claude-launch / codex-launch
      └── Codex default: codex-app-worker ──stdio JSON-RPC──► codex app-server
                                                               (YOLO policy)
```

There is **no LLM in the bridge**. `agent-bridge` is a deterministic pipe: it
maps each Discord channel 1:1 to one tmux-backed worker, forwards channel
messages into the worker and posts replies back out. Claude/TUI workers retain
the keystroke path; app-server workers use a host-local Unix control socket.
All reasoning happens inside the selected subscription-backed agent.

Claude Code sends two things back through hooks:

- **Replies** — when a worker turn ends, the **Stop** hook
  (`claude-worker-done-relay`) wakes the bridge, which extracts the final reply
  text from the transcript and posts it into the worker's repo channel.
- **Live task checklists** — a **PostToolUse** hook
  (`claude-worker-todo-relay`) fires on every todo/task update and relays the
  current checklist into Discord as checkboxes.

Codex app-server needs no Codex hooks. Its protocol notifications supply the
final reply plus a live stream of UI-safe reasoning summaries, plan updates,
command/tool/file activity, diff stats, token usage, typed failures, goals, and
rate limits. `codex-app-worker` signs those events to the same bridge listener.
The bridge edits one Discord progress card in place for the turn, then posts
the final answer as a separate, durable channel message. Steering updates the
active turn and its existing card; a later independent turn gets a new card.
The activity feed uses structured intent labels and fenced commands. It never
puts raw reasoning or raw command output in the progress card.

On a satellite worker, reply extraction and usage collection happen beside the
transcript. A legacy done relay or the app-server worker includes the reply plus
a compact token snapshot in its signed event; this keeps replies, `/cost`,
subagent accounting, and guest budgets at parity without mounting the
satellite's session files on the parent.

Idle workers (idle longer than `idle_minutes`) are stopped; the next message
resumes the provider conversation. Codex app-server resumes its persisted
thread id; Claude uses `--continue`.

## 2. Hook registration in `~/.claude/settings.json`

The relays only run if Claude Code is told to invoke them. Claude Code reads
hook configuration from `~/.claude/settings.json`. Hooks are grouped by event
name; each event maps to a **list of entries**, and each entry has an optional
`matcher` (matched against the tool name for tool events) plus a `hooks` list of
`{"type": "command", "command": "..."}` actions. The `command` values here are
just the tool names — `scripts/link.sh` links both onto your `PATH` in
`~/.local/bin`, so no absolute path is needed.

Add both hooks:

- A **`Stop`** hook running `claude-worker-done-relay`. `Stop` fires once per
  finished worker turn (task done, or stopped to ask a question). It has no
  meaningful matcher, so use `""` (or omit `matcher` entirely).
- A **`PostToolUse`** hook with matcher `"TodoWrite|TaskCreate|TaskUpdate"`
  running `claude-worker-todo-relay`. The matcher is a regular expression over
  the tool name, so this one entry covers todo-list writes and all task
  create/update calls.

Complete minimal `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          { "type": "command", "command": "claude-worker-done-relay" }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "TodoWrite|TaskCreate|TaskUpdate",
        "hooks": [
          { "type": "command", "command": "claude-worker-todo-relay" }
        ]
      }
    ]
  }
}
```

If you already have a `settings.json`, merge these into your existing `hooks`
object rather than replacing the file. Both relays are safe by design: each
only acts for orchestrated workers (those started by `agent-worker`, which sets
`$CLAUDE_WORKER`), a manual `claude` session stays silent, and each always exits
`0` so a relay failure can never break the Claude session.

## 2b. Codex app-server (default)

No Codex hook profile is needed on the default backend. `codex-app-worker`
starts `codex app-server --stdio`, sends the initialize/initialized handshake,
and starts or resumes a thread with `approvalPolicy=never` and
`danger-full-access`. It strips API-key environment variables before launch,
so authentication stays on the normal `codex login` / ChatGPT subscription
path.

Its state lives beside the existing worker metadata:

```
~/.local/state/claude-workers/<name>/
  meta                       backend=app-server, control_socket=...
  app-server.json            thread/model/turn/plan/usage state
  app-server-events.jsonl    rotating mode-0600 host-local protocol log
  control.sock               mode-0600 send/inspect/interrupt API
  output.log                 tmux/web-console terminal output
```

The tmux shell contract stays intact for the separate worker web UI: list,
status, start, send, screen capture, logs, stop/restart, and terminal attach all
continue to go through `agent-worker`. Existing TUI workers are not converted
by a daemon restart. `codex_backend: "tui"` globally, or `backend: "tui"` on
one repo, is the rollback path for its next start.

## 2c. Legacy Codex TUI profile (`~/.codex/worker.config.toml`)

A channel explicitly using `backend: "tui"` needs the Codex equivalent of the
`Stop` relay. Codex fires its own **`Stop`** hook at the end of every turn, and
its payload carries the final assistant message inline — so `codex-worker-done-relay`
reads it and POSTs the same `claude.worker.turn_ended` event, reply text and all
(no transcript parsing).

To keep the hook out of your everyday `codex` sessions — where it would trigger a
"Hooks need review" prompt — it lives in a **profile**, not your base
`~/.codex/config.toml`. `codex-launch` starts workers with `-p worker`
(and `--dangerously-bypass-hook-trust`, so it runs unattended), which layers
`~/.codex/worker.config.toml` on top of your base config for workers only.

Install the profile (a template ships in this repo):

```bash
cp codex-profiles/worker.config.toml ~/.codex/worker.config.toml
```

It contains just:

```toml
[hooks]
Stop = [ { hooks = [ { type = "command", command = "codex-worker-done-relay" } ] } ]
```

Like the Claude relay, `codex-worker-done-relay` only acts for chat-bound workers
(`$CLAUDE_WORKER` set + a `chat=` in the worker meta), skips `stop_hook_active`
continuations to avoid loops, and always exits `0`. Without the profile a Codex
worker still runs — it just falls back to the worker's own `discord-notify` for
replies (no silent-turn safety net). Your base `~/.codex/config.toml` is never
modified.

Provider quota refusal is the exception to the legacy Stop-hook path: Codex can
remain on its hard-limit screen without firing Stop at all. The bridge therefore arms
a lightweight pane watcher for every delivered Claude/Codex turn. A hard-limit
message is posted once to the mapped Discord channel with its reset time; an
ordinary approaching-limit reminder is not. If Codex also opens its cheaper-
model suggestion, `agent-worker` selects **Keep current model** so the harness
does not silently alter the channel's configured model.

The same startup driver handles Claude's one-time Bypass Permissions warning.
Because the full-trust worker was explicitly launched with bypass enabled, it
selects **Yes, I accept** and waits for the real input prompt before the bridge
pastes the first Discord message.

## 2d. Antigravity has no relay yet

`/harness antigravity` starts a worker through `antigravity-launch`, and
everything *inbound* works — the bridge pastes messages into the pane, and
`$CLAUDE_WORKER` is set, so the worker's own `discord-notify` calls
auto-target its channel.

Nothing *outbound* is automatic. There is no `antigravity-worker-done-relay`
and no hook registration for `agy`, so no `claude.worker.turn_ended` event is
ever POSTed for an antigravity worker. That means:

- no turn-end reply is posted unless the worker volunteers one via
  `discord-notify`;
- neither fallback that covers a quiet worker applies — Claude's transcript
  parse needs a `transcript_path` from its relay, and Codex's inline text
  rides on the relay's payload. Without an event, neither runs;
- the `PostToolUse` todo relay doesn't fire either, so no live checklists.

Wiring this up needs whatever hook mechanism `agy` actually exposes, which is
why it isn't done here rather than guessed at. Until then the engine is
experimental: a turn can end in silence, and the operator has to `/screen` to
see what happened.

## 3. Secret files under `~/.config/claude-workers/`

Create these three files and `chmod 600` each — they are read for content only,
never printed, never logged, and never passed through the environment.

| File | Contents |
|---|---|
| `discord-bot-token` | The raw Discord bot token, one line, nothing else. |
| `bridge-webhook` | The bridge listener endpoint and shared HMAC secret (see below). |
| `discord-webhook` | A plain Discord channel webhook URL, one line. Main-channel fallback used by the todo relay when a worker has no mapped repo channel. |

`bridge-webhook` holds two `KEY=value` lines:

```
BRIDGE_WEBHOOK_URL=http://127.0.0.1:8765/event
BRIDGE_WEBHOOK_SECRET=<hex>
```

The URL must match the bridge's `listen_port`. `BRIDGE_WEBHOOK_SECRET` is the
**single shared secret** used by the bridge and by every hook/client to sign and
verify events (HMAC-SHA256 over the request body). Generate it once:

```sh
openssl rand -hex 32
```

Put that same value in this file; the bridge reads it from here too, so there is
nothing else to configure.

Example setup:

```sh
umask 077
mkdir -p ~/.config/claude-workers

printf '%s\n' 'YOUR_DISCORD_BOT_TOKEN'            > ~/.config/claude-workers/discord-bot-token
printf '%s\n' 'https://discord.com/api/webhooks/…' > ~/.config/claude-workers/discord-webhook

{
  echo 'BRIDGE_WEBHOOK_URL=http://127.0.0.1:8765/event'
  echo "BRIDGE_WEBHOOK_SECRET=$(openssl rand -hex 32)"
} > ~/.config/claude-workers/bridge-webhook

chmod 600 ~/.config/claude-workers/discord-bot-token \
          ~/.config/claude-workers/discord-webhook \
          ~/.config/claude-workers/bridge-webhook
```

The bridge's own `config.json` (category id, allowed users, `idle_minutes`,
`listen_port`, and the `repos` channel→dir map) is documented in the
[README](../README.md#configuration) — see there for its schema rather than
duplicating it here.

## 4. The `claude-launch` dependency contract

`claude-launch` is **not in this repo.** It lives in the private
[nedlane/dotfiles](https://github.com/nedlane/dotfiles) repo, and `agent-worker`
locates it via `$DOTFILES_DIR`/`PATH` (falling back to
`$DOTFILES_DIR/shared/bin/claude-launch`). If it is missing, `agent-worker`
dies with an error telling you to run `scripts/link.sh` — but linking cannot
supply a file this repo does not ship. **Without `claude-launch`, only bare
`claude` workers run and the per-channel capability profiles do not function.**

If you need to reimplement it for a standalone install, it must:

**Launch on the subscription auth path, never API/provider billing.** It starts
interactive `claude` using the Claude *subscription* login, and it strips any
inherited `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, and
the Bedrock/Vertex switches (`CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`)
from the child environment, so a worker can never silently fall onto API or
cloud-provider billing.

**Accept the flags the bridge and worker rely on:**

| Flag | Used for |
|---|---|
| `--label <text>` | Tag the session (`agent-worker` passes `worker:<name>`). |
| `--continue` | Resume the worker's previous session (idle revival). |
| `--append-system-prompt <text>` | Append the per-worker brief the bridge builds. |

**Profile flags** (how a channel's capability profile is enforced):

| Flag | Meaning |
|---|---|
| `--enforce-perms` | Turns **off** `--dangerously-skip-permissions`, so the settings-file allow/deny rules actually apply. The `utility` and `greeter` profiles set this; `owner` and `collab` do not (both are full trust and bypass prompts). |
| `--tools <list>` | Restrict which built-in tools are available (empty string = none). |
| `--mcp-config <file>` | Load a specific MCP server config for the profile. |
| `--strict-mcp-config` | Use only the given MCP config, ignoring any others. |
| `--allowedTools <list>` | Allowlist of tools/MCP methods the worker may call. |
| `--settings <file>` | Point Claude Code at the profile's settings file. |
| `--permission-mode <mode>` | Set the permission mode (`default`, `acceptEdits`, …). |

These map directly onto the profiles in `claude-profiles/` (owner / collab /
utility / greeter): `owner` and `collab` pass no extra flags (both full trust);
the restricted profiles (`utility`, `greeter`) combine `--enforce-perms`,
`--settings`, `--permission-mode`, and `--mcp-config`/`--strict-mcp-config`/
`--allowedTools`/`--tools`.

## 5. Verify

Run **`agent-checkup`** at any time. It reports on Claude Code and its
subscription auth mode, tmux, the bridge runtime + credentials, the linked
worker tooling, and the state directory, and it prints the manual checks it
cannot perform automatically (Discord intents, live auth, and confirming these
hooks are registered).
