# The local agent control plane

This document describes how a Discord message becomes work done by an
interactive Claude Code session on this machine, and — most importantly — the
**Claude Code hook setup** the tools in this repo point back here for.

## 1. Overview / data flow

```
Discord channel  (one channel per repo, under a "Claude" category)
      │
      ▼
claude-bridge          discord.py client + a signed localhost HTTP listener
                       (aiohttp) bound to 127.0.0.1:<listen_port>, default 8765
      │  forwards your message with `claude-worker send`
      ▼
claude-worker          worker lifecycle over a tmux session named `cw-<name>`
      │  launches / resumes the session
      ▼
claude-launch          wrapper that starts interactive `claude` on the Claude
      │                subscription auth path (see §4)
      ▼
Claude Code (interactive)   the only intelligence in the loop
```

There is **no LLM in the bridge**. `claude-bridge` is a deterministic pipe: it
maps each Discord channel 1:1 to one tmux-backed worker, forwards channel
messages into the worker as keystrokes, and posts replies back out. All
reasoning happens inside Claude Code, running on the Claude subscription.

Two things flow back to Discord, and both are driven by **Claude Code hooks**
(not by polling):

- **Replies** — when a worker turn ends, the **Stop** hook
  (`claude-worker-done-relay`) wakes the bridge, which extracts the final reply
  text from the transcript and posts it into the worker's repo channel.
- **Live task checklists** — a **PostToolUse** hook
  (`claude-worker-todo-relay`) fires on every todo/task update and relays the
  current checklist into Discord as checkboxes.

Idle workers (idle longer than `idle_minutes`) are stopped; the next message
revives them with `claude --continue`, restoring the conversation from Claude
Code's own session history.

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
only acts for orchestrated workers (those started by `claude-worker`, which sets
`$CLAUDE_WORKER`), a manual `claude` session stays silent, and each always exits
`0` so a relay failure can never break the Claude session.

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
[nedlane/dotfiles](https://github.com/nedlane/dotfiles) repo, and `claude-worker`
locates it via `$DOTFILES_DIR`/`PATH` (falling back to
`$DOTFILES_DIR/shared/bin/claude-launch`). If it is missing, `claude-worker`
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
| `--label <text>` | Tag the session (`claude-worker` passes `worker:<name>`). |
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
