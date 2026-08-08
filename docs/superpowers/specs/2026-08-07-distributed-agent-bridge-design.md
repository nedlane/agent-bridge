# Distributed agent-bridge — design

- **Date:** 2026-08-07
- **Status:** Approved (ready for implementation plan)
- **Author:** Christian (C-Nucifora)

## Problem

`agent-bridge` runs one Discord bot on **dev-box** and maps each Discord channel
to a local tmux Claude/Codex worker. Every worker therefore runs on dev-box.
Some work needs a *different* machine — macOS/Xcode/iOS builds on the **mac**,
Arch-specific tooling on **archbtw** — but there is no way to run a first-class
worker on another machine and drive it from the same Discord.

## Goals

- Run real workers on satellite machines (mac, archbtw, and any future tailnet
  node) that are **indistinguishable** from local workers to the Discord user:
  send messages, `discord-notify` replies, 👀 peek (in colour), todo relays,
  `/model` `/fast` `/compact` `/clear` `/stop` `/restart`, and file attachments
  in both directions.
- Keep **one** Discord bot and **one** orchestrator that sees the whole fleet.
- Leave existing local-only setups **byte-for-byte unchanged** unless they opt
  in.

## Non-goals

- No browser/web console (that is a separate project; may share ideas later).
- No per-satellite long-running daemon — we reuse the `agent-worker` CLI already
  deployed on every machine.
- No automatic repo/file sync between machines — a remote worker works on files
  that already live on its own machine.

## Architecture — SSH command-dispatch

The parent bridge on dev-box stays the sole Discord owner and holds the entire
channel↔worker map. A repo mapping gains a **`host`** field. Worker actions,
which already all funnel through the `agent-worker` CLI, are dispatched to the
worker's host: locally via `subprocess` (as today) or remotely via
`ssh <target> -- agent-worker …`. Workers' return traffic (`discord-notify`,
done/todo relays) reaches the parent's event listener over the tailnet, secured
by the existing HMAC secret.

```
Discord ── bridge (dev-box) ──local──> agent-worker ── tmux worker
                  │
                  └──tailscale ssh──> mac/archbtw: agent-worker ── tmux worker
                                             │ discord-notify / done / todo
                  <──── tailnet POST /event (HMAC) ─────┘
```

Why this shape: `agent-worker`, `claude-launch`, and the colour-aware
`term-shot` are already deployed on all three machines, and the bridge already
does 100% of worker I/O through `agent-worker`. So most parity is achieved by
making command dispatch host-aware plus moving the return path off loopback.

## Components

### 1. Placement model

- `config.json` repo entries gain optional `"host": "<machine>"` (default
  `"local"`).
- New top-level `"machines"` map resolves a logical name to an SSH target and
  metadata, e.g.:
  ```json
  "machines": {
    "mac":     { "ssh": "christiannucifora@hc-002" },
    "archbtw": { "ssh": "archbtw" }
  }
  ```
- `"local"` is implicit and needs no entry.

### 2. Host-aware command dispatch

A single wrapper `run_worker_cmd(host, argv, **kw)`:

- `host == "local"` → `subprocess.run(argv, …)` (today's behaviour, unchanged).
- remote → `subprocess.run(["ssh", target, "--", *shlex-quoted argv], …)`,
  where `target` comes from the `machines` map. Env that the local path sets
  (e.g. `CLAUDE_WORKER`) is forwarded as an explicit `env NAME=VAL` prefix in the
  remote command, because SSH does not carry it.

All of `worker_start / worker_send / worker_key / worker_screen / worker_stop`
route through this wrapper. Because peek, `/slash` commands, lifecycle, etc. are
already expressed as `agent-worker` calls, they gain remote support for free.

**Peek:** the parent runs `agent-worker read <name> 40 --ansi` on the worker's
host, then pipes the captured (ANSI) text into its **local** `term-shot` to
produce the colour PNG. No file is copied.

### 3. Return path over the tailnet

Today the listener binds `127.0.0.1:8765` and relays target
`http://127.0.0.1:8765/event`. Changes:

- **Listener bind:** new top-level `"listen_host"` config key (default
  `"127.0.0.1"` for backward compatibility). To enable remote workers, set it to
  dev-box's tailscale IP (or `0.0.0.0`). `web.TCPSite(runner, cfg["listen_host"],
  cfg["listen_port"])`.
- **Satellite webhook config:** each satellite's
  `~/.config/claude-workers/bridge-webhook` sets
  `BRIDGE_WEBHOOK_URL=http://100.105.249.62:8765/event` (dev-box's tailnet IP)
  with the **same** `BRIDGE_WEBHOOK_SECRET`. Then `discord-notify` (bridge path),
  `done-relay`, and `todo-relay` from a satellite reach the parent.
- The worker's `meta` (carrying `chat=discord:<channel_id>`) is written **on the
  satellite** by the SSH-dispatched `agent-worker start`, so the relays that read
  `$STATE_ROOT/<worker>/meta` find their channel target with no change.

### 4. Relay inline reply extraction (removes the transcript dependency)

Today, on `claude.worker.turn_ended` the bridge reads the worker's **transcript
file off local disk** to extract the reply for turns where the worker did not
`discord-notify`. That file lives on the satellite for a remote worker.

Fix (unifies the Claude path with the existing Codex path, which already sends
its reply inline): move last-reply extraction into
`claude-worker-done-relay`, which runs **on the worker's machine**. The relay
emits the reply text (and its incremental byte-offset bookkeeping) inline in the
`turn_ended` payload; the bridge just posts what it receives. This removes the
localhost transcript assumption for **all** workers, local and remote.

- The per-transcript byte-offset state moves next to the worker (its own
  machine), which is where the transcript already is.
- The bridge's `extract_last_reply` logic moves into a helper the
  `done-relay` invokes (so the extraction runs where the transcript lives); the
  bridge keeps a thin guard that posts nothing on a missing/empty inline reply.
  For local workers the result is identical to today's bridge-side extraction
  (asserted by tests).

### 5. Attachments

- **Inbound** (a user attaches a file in a remote worker's channel): the bridge
  `scp`s the saved file to `‹target›:$STATE_ROOT/<worker>/inbox/` before
  delivering the message, via a host-aware branch in the deliver path. Local
  workers keep the current in-place save.
- **Outbound** (`discord-notify -i file.png` on a satellite): the file is on the
  satellite and the bridge cannot read a remote path. `discord-notify` uploads
  the bytes to the bridge's `/event` endpoint as multipart (still HMAC-signed);
  the bridge forwards the attachment to Discord through the bot. This keeps all
  Discord egress on the single bot and needs no per-channel webhook on
  satellites. Local `discord-notify` keeps sending a path the bridge reads.

### 6. Security

- The listener leaves loopback, so it relies on: HMAC-SHA256 on every event
  (already enforced and unchanged), binding the **tailscale interface** (never a
  public interface), and tailscale ACLs restricting which nodes may reach
  dev-box's `listen_port`.
- SSH dispatch uses the existing dev-box→satellite tailscale-SSH trust.
- The same `BRIDGE_WEBHOOK_SECRET` must be present on dev-box and every
  satellite (it already is on dev-box).

### 7. Config / UX

- `config.json`: repo `host` field, `machines` map, `listen_host` key (all
  optional; absent → today's behaviour).
- `bridge-ctl addrepo <name> </abs/path> [category] [--host <machine>]`, and the
  `/addrepo` slash command gains an optional host argument. `bridge-ctl repos`
  shows each mapping's host.
- `agent-checkup` extended to probe each configured satellite: SSH reachable,
  `agent-worker` on PATH, and its `bridge-webhook` points back at the parent.

## Data flow — one remote turn

1. User messages `#app` (mapped to `host=mac`). Bridge tags the turn with the
   sender identity and `run_worker_cmd("mac", ["agent-worker","send","app",…])`.
2. `agent-worker send` types into the mac's tmux worker over SSH.
3. Worker works; on turn end its `done-relay` (on the mac) extracts the reply and
   POSTs `turn_ended` (reply inline, HMAC-signed) to
   `http://<dev-box>:8765/event`.
4. Bridge posts the reply to `#app`. Todo updates arrive the same way via
   `todo-relay`; `discord-notify` messages arrive as `claude.worker.send`.

## Error handling / failure modes

- **Satellite unreachable (SSH fails):** `run_worker_cmd` surfaces a clear
  error to the channel ("`app` is on `mac`, which is unreachable"), mirroring how
  a wedged local worker is reported. No silent hang.
- **Relay can't reach the parent:** relays already exit 0 and (for
  `discord-notify`/`todo-relay`) fall back to a direct channel webhook when
  configured; a lost `turn_ended` degrades to "no auto-summary", same as today
  when the bridge is down.
- **Clock/secret mismatch:** a bad HMAC is rejected by the listener (already
  enforced); surfaced in bridge logs.

## Backward compatibility

With `listen_host` absent (→ `127.0.0.1`), no `machines`, and every repo
`host` defaulting to `local`, the system behaves exactly as before. The
relay-side inline extraction is the only change that touches the local path; it
must produce output identical to today's bridge-side extraction (covered by
tests).

## Testing

- **Unit:** `run_worker_cmd` builds correct local vs `ssh` argv (incl. env
  forwarding and quoting); host/machine resolution; config parsing of
  `host`/`machines`/`listen_host`; relay inline-extraction matches the old
  bridge-side extraction on recorded transcripts.
- **Integration (scripted, mac + archbtw):** create a remote repo → start →
  send → reply lands in Discord → colour peek returns a PNG → todo relay posts →
  `/model`/`/stop`/`/restart` → inbound & outbound attachment. 
- **Backward-compat:** a local-only config path produces byte-identical
  behaviour (listener on loopback, in-place attachment save, same reply text).

## Milestones (built together, in this order)

1. **Dispatch + placement:** `run_worker_cmd`, `host` field, `machines` map;
   remote start/send/stop/restart/`/slash`; peek over SSH + local `term-shot`.
2. **Return path:** `listen_host` bind; satellite `bridge-webhook` pointing at
   the parent; relay inline reply extraction (unify Claude with Codex).
3. **Attachments:** inbound `scp` to remote inbox; outbound multipart upload to
   `/event`.
4. **Ops:** `bridge-ctl addrepo --host` + `/addrepo` host arg; `bridge-ctl
   repos` host column; `agent-checkup` satellite probes.

## Risks / open questions

- **Relay extraction parity:** relocating `extract_last_reply` must exactly match
  current output (incremental offsets, Claude vs Codex). Guarded by tests.
- **SSH latency:** ~100–300 ms per command; acceptable for human-paced actions.
  If a hot path emerges (e.g. rapid peeks), consider a persistent SSH control
  master (`ControlMaster`) — noted, not built.
- **Tailnet exposure:** binding `0.0.0.0` vs the tailscale IP — prefer the
  tailscale IP; confirm the interface name/address is stable across reboots.
