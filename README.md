# agent-bridge

A Discord-driven control plane for interactive Claude Code and Codex workers.

`agent-bridge` maps each Discord channel (under a **Claude** category) 1:1 to a
persistent tmux-backed worker running either **Claude Code** or **Codex** (in
YOLO mode): one channel per repo, one worker per repo, no threads. `/harness`
switches a channel's engine. Your channel messages are forwarded into the
worker; the worker's replies and live task checklists are relayed back out
through engine hooks; each channel runs under a per-channel capability profile
(`owner` / `collab` / `utility` / `greeter`). **There is no LLM inside the
bridge** — the agent (Claude Code or Codex), on your subscription, is the only
intelligence in the loop. The bridge is deterministic plumbing around it. (The
daemon and worker tools were renamed `claude-bridge`→`agent-bridge` and
`claude-worker`→`agent-worker`; the old names remain as symlinks.)

It is desktop-only tooling, originally extracted from
[nedlane/dotfiles](https://github.com/nedlane/dotfiles) (where it lives as a git
submodule under `hosts/wsl-desktop/agent-bridge/`) and now runnable standalone.
MIT-licensed — see [`LICENSE`](LICENSE).

## Layout

| Path | What |
|---|---|
| `bin/agent-bridge` | Python daemon: Discord ↔ worker pipe (discord.py + signed-event HTTP listener) |
| `bin/agent-worker` | Worker lifecycle over tmux sessions (`cw-<name>`) |
| `bin/bridge-ctl` | Thin signed client: add repos/guests, request approvals |
| `bin/discord-notify` | Post a message/file to a Discord channel from the host |
| `bin/claude-worker-todo-relay` | PostToolUse/TodoWrite hook → live task checkboxes to Discord |
| `bin/claude-worker-done-relay` | Stop hook → completion push to Discord |
| `bin/agent-checkup` | Readiness / auth-mode report |
| `bin/term-shot` | Captured terminal → text/image (needs Pillow) |
| `claude-profiles/` | Per-channel capability profiles (`*.settings.json`, `*.mcp.json`) |
| `skills/` | Skills: `claude-bridge`, `discord-notify` |
| `systemd/claude-bridge.service` | User service for the daemon |
| `scripts/link.sh` | Symlinks `bin/*`, `skills/*`, and the systemd unit into place |

## Requirements

The host that runs the bridge needs:

- **Python 3** with **[discord.py](https://discordpy.readthedocs.io/)**
  (`pip3 install --user discord.py`) — the daemon's runtime.
- **[Pillow](https://python-pillow.org/)** (`pip3 install --user Pillow`) — used
  by `bin/term-shot` to render a worker's live screen as an image for the 👀
  peek and `/screen`. Without it, peeks fall back to a text code block.
- **[brotlicffi](https://pypi.org/project/brotlicffi/)**
  (`pip3 install --user brotlicffi`) — recommended so aiohttp can decode
  brotli-encoded Discord CDN responses. The bridge downloads uploaded files with
  `Accept-Encoding: identity` to sidestep this, so it is not strictly required,
  but installing it is the robust belt-and-suspenders fix (the Google `brotli`
  build is incompatible with recent aiohttp and otherwise breaks file uploads).
- **tmux** — every worker is a detached `cw-<name>` tmux session.
- **jq** — used by CI and handy for validating config/profile JSON.
- **curl** — the signed-event clients (`bridge-ctl`, `discord-notify`) POST with it.
- The **`claude` CLI** ([Claude Code](https://docs.claude.com/en/docs/claude-code)),
  logged in interactively on a **Claude subscription** (not an API key, not
  Bedrock/Vertex). Run `claude` once and `/login` first.
- **`claude-launch`** — a launcher wrapper that this repo does **not** include.

### About `claude-launch` (external dependency)

`agent-worker` starts every worker through a wrapper called `claude-launch`,
resolved from `PATH` or `$DOTFILES_DIR/shared/bin/claude-launch`. It lives in
the **private [nedlane/dotfiles](https://github.com/nedlane/dotfiles) repo and
is not bundled here.** Be aware, honestly:

- **Without `claude-launch` on PATH, workers cannot start.** `agent-worker
  start` fails fast with `claude-launch not found`.
- The restricted capability profiles pass `claude-launch`-specific flags that
  stock `claude` does **not** accept — `--enforce-perms`, `--tools`,
  `--mcp-config`, `--strict-mcp-config`, `--allowedTools`, `--settings`, and
  `--append-system-prompt`. So the `utility` and `greeter` profiles
  **require `claude-launch`**; the `owner` and `collab` profiles (both full
  trust, adding no flags) could work against a stock `claude` binary via a shim,
  and even then the
  Discord protocol is injected with `--append-system-prompt`.

If you don't have `claude-launch`, you'll need to supply your own wrapper on
PATH that accepts those flags (or restrict yourself to owner channels and adapt
`start_args()`/`profile_args()` accordingly). This is the one piece a stranger
cannot get purely from this repo.

## Standalone install

This installs the bridge directly on a host, without the dotfiles submodule.

1. **Install dependencies** (see [Requirements](#requirements)): Python 3,
   discord.py, Pillow, tmux, jq, curl, the `claude` CLI (logged in), and a
   `claude-launch` wrapper on PATH.

2. **Link the tools into place.** Run the bundled linker:

   ```bash
   scripts/link.sh
   ```

   It symlinks `bin/*` into `~/.local/bin`, `skills/*` into `~/.claude/skills`,
   and `systemd/claude-bridge.service` into `~/.config/systemd/user`. To do it
   by hand instead, create those symlinks yourself. Make sure `~/.local/bin` is
   on your `PATH`.

3. **Create the config and secrets** — see
   [Configuration](#configuration) and [Secrets](#secrets) below.

4. **Register the Claude Code hooks** so worker replies and live checklists get
   relayed back to Discord. `claude-worker-done-relay` runs on the **Stop** hook
   and `claude-worker-todo-relay` on the **PostToolUse** hook
   (`TodoWrite|TaskCreate|TaskUpdate`). See
   [`docs/agent-control-plane.md`](docs/agent-control-plane.md) for the exact
   `~/.claude/settings.json` snippets.

5. **Enable the service:**

   ```bash
   systemctl --user enable --now claude-bridge
   ```

   (Consider `loginctl enable-linger $USER` so it survives logout.)

6. **Verify** the whole plane with:

   ```bash
   agent-checkup
   ```

   It reports on tmux, the `claude` subscription auth path, discord.py, the
   config and secret files, the linked tools, and prints the manual checks it
   can't automate (Discord intents, live auth, hook registration).

### Running the daemon standalone (debugging)

You don't need the service to run it. For a foreground session with logs on
your terminal:

```bash
python3 bin/agent-bridge --config ~/.config/claude-bridge/config.json
```

The config path defaults to `~/.config/claude-bridge/config.json` and can also
be set with the `CLAUDE_BRIDGE_CONFIG` environment variable.

## Discord bot setup

1. In the [Discord developer portal](https://discord.com/developers/applications)
   create an **application**, then add a **bot** to it.
2. Under the bot settings, enable the **Message Content** privileged intent —
   the bridge sets `intents.message_content` and `intents.reactions`, and
   without Message Content it can't read what you type.
3. Invite the bot to your server with **both** OAuth2 scopes: **`bot`** and
   **`applications.commands`**. The slash commands will **not** register without
   `applications.commands`.
4. Grant the bot these permissions:
   - **Manage Channels** — create and delete repo channels (`/addrepo`, `/close`).
   - **Manage Messages** — `/clear` and `/fresh` purge the channel's history.
   - **Send Messages** — post worker replies.
   - **Read Message History** — relay context and purge old messages.
   - **Add Reactions** — pre-seed the 👁️ / ✏️ / ❌ decision reactions on request cards.
   - **Manage Roles** — set the per-member channel permission overwrites that
     grant guests view/edit access (`do_addguest`).
5. Create a category (e.g. named **Claude**) to hold your repo channels. Copy
   its channel id into `category_id`.
6. Copy **your own** Discord user id into `allowed_users` (owner). Enable
   Developer Mode in Discord to right-click → *Copy ID*.

## Configuration

The bridge reads a single JSON file at `~/.config/claude-bridge/config.json`
(override with the `CLAUDE_BRIDGE_CONFIG` environment variable). Fields
(defaults come from `default_config()`):

| Field | Type | Meaning |
|---|---|---|
| `category_id` | int | Channel id of the **Claude** category new repo channels are created under. |
| `allowed_users` | int[] | Owner Discord user ids. Gate every slash command and every owner-only action (reaction decisions, guest management). |
| `idle_minutes` | int | Idle workers are stopped after this many minutes (default `45`). The next message revives them with `--continue`. |
| `listen_port` | int | Port of the localhost signed-event listener (default `8765`). Must match the port in `bridge-webhook`. |
| `repos` | object | Map of **channel id (string)** → repo object (below). |
| `welcome_channel` | int \| null | Channel id of the public `#welcome` greeter (open to any member). `null` disables it. |
| `requests_channel` | int \| null | Channel id where guest-access approval cards are posted. `null` disables it. |

Each entry in `repos` is keyed by the Discord channel id (as a string) and holds:

| Key | Type | Meaning |
|---|---|---|
| `name` | string | Worker/channel name (tmux session `cw-<name>`, state dir). |
| `dir` | string | Absolute path to the repo the worker runs in. |
| `profile` | string | Capability profile: `owner` (default), `collab`, `utility`, or `greeter`. |
| `guests` | int[] | Editor guest ids (View + Send — can drive the worker). Optional. |
| `viewers` | int[] | View-only guest ids (can watch, can't drive). Optional. |

Repos are normally created with the `/addrepo` slash command (which needs the
bot already running and writes the entry for you), so a first-run config can
start with an empty `repos` object. A complete example:

```json
{
  "category_id": 111111111111111111,
  "allowed_users": [222222222222222222],
  "idle_minutes": 45,
  "listen_port": 8765,
  "welcome_channel": 333333333333333333,
  "requests_channel": 444444444444444444,
  "repos": {
    "555555555555555555": {
      "name": "myrepo",
      "dir": "/home/you/projects/myrepo",
      "profile": "owner"
    },
    "666666666666666666": {
      "name": "shared-thing",
      "dir": "/home/you/guest-workspaces/shared-thing",
      "profile": "collab",
      "guests": [777777777777777777],
      "viewers": [888888888888888888]
    }
  }
}
```

## Secrets

Three files live under `~/.config/claude-workers/`. Create the directory, write
each file, and `chmod 600` all of them:

### `discord-bot-token`

The raw Discord bot token, on a single line (read with `f.read().strip()`).
Copy it from your application's **Bot** page in the developer portal.

```
MTIzNDU2Nzg5...your.bot.token...
```

### `bridge-webhook`

`key=value` lines giving the URL and shared secret of the bridge's localhost
event listener:

```
BRIDGE_WEBHOOK_URL=http://127.0.0.1:8765/event
BRIDGE_WEBHOOK_SECRET=<random hex>
```

- The **same `BRIDGE_WEBHOOK_SECRET`** must be used by the bridge and by every
  client that posts to it — `bridge-ctl`, `discord-notify`, and the hook relays
  all HMAC-sign their events with it. Generate one with, e.g.,
  `openssl rand -hex 32`.
- The URL's port must match `listen_port` in the config (`8765` by default).

### `discord-webhook`

A plain Discord channel **webhook URL** on one line (create it in the channel's
*Integrations → Webhooks* settings). It's the **main-channel fallback**: when a
message isn't bound to a repo channel, `discord-notify` and the todo relay post
here instead.

```
https://discord.com/api/webhooks/1234567890/abcdef...
```

## Slash commands

Control verbs are native Discord **application (slash) commands**, synced
per-guild when the bot connects. They're restricted to `allowed_users` (owner).
On every command the `worker` option defaults to the worker mapped to the
channel you run it in.

| Command | What it does |
|---|---|
| `/status` | List all workers, their running state, and engine (claude/codex). |
| `/harness <claude\|codex> [worker]` | Switch a channel between **Claude Code** and **Codex** (Codex runs in YOLO mode — bypass all approvals + sandbox, the analog of Claude's bypass-permissions). Stops the worker; the next message starts it on the new engine. Each engine keeps its **own** conversation, so switching back resumes that engine's last thread. |
| `/stop [worker]` | Stop a worker (state kept; a message revives it). |
| `/restart [worker]` | Restart a worker, resuming its conversation. |
| `/screen [worker]` | Post the worker's live TUI screen (image, or a code-block fallback). |
| `/model <model> [worker]` | Switch the worker's model (e.g. `opus`, `sonnet`, `haiku`). On Codex, `/model` opens an interactive picker — drive it via `/screen`. |
| `/clear [worker]` | Fresh context **now**: restart without `--continue`. **Also purges the channel's messages.** |
| `/fresh [worker]` | Shut down and arm a fresh start: the next message begins a new session (lazy, no resume). **Also purges the channel's messages.** |
| `/compact [focus] [worker]` | Compact the worker's context (optional focus hint). |
| `/checkin [worker]` | Ask a running worker to send a 3–5 line progress update. |
| `/addrepo <name> <path> [category]` | Create `#<name>` and map it to a repo directory. Optional `category` files it under an existing category (matched loosely, ignoring emoji/case) or creates a new one; omitted, it lands in the default inbox category. |
| `/close [worker] confirm:<name>` | **Irreversible teardown** — stop the worker, wipe its saved state, and delete its channel. Requires retyping the worker name in `confirm`. |
| `/addguest <name> <discord_id> [edit\|view]` | Grant a guest edit (View+Send) or view (read-only) access to one channel. |
| `/lockdown` | Drop **all** guests everywhere to view-only in one shot (leaves the owner untouched). |

Notes:

- `/clear` and `/fresh` purge the channel and therefore need the bot's **Manage
  Messages** permission.
- `/close` deletes the channel and its whole history — it needs **Manage
  Channels** and won't proceed unless `confirm` exactly matches the worker name.
- A message beginning with `/ignore` is a **channel-only note**: the bridge
  drops it (marking it 🙈) instead of relaying it, so you can jot something in a
  worker's channel without the worker picking it up.
- Any **other** message beginning with `/` (i.e. not one of the commands above)
  is typed straight into the worker as keystrokes, so any Claude Code slash
  command (`/help`, `/context`, …) still works from Discord.

## Guest access & the public front desk

The bridge has a lightweight "front desk" for letting other server members work
on a project without giving them owner rights:

- A public **`#welcome`** greeter channel (config `welcome_channel`) is open to
  anyone in the server. A `greeter`-profile worker there helps a visitor pick a
  project and files an access request.
- Requests land as **approval cards** in a **requests channel** (config
  `requests_channel`). The owner reacts to decide: 👁️ grants view-only, ✏️
  grants edit, ❌ denies. Granting sets a per-member Discord permission
  overwrite on that project's channel and records the guest in `guests`
  (editors) or `viewers`.
- The owner can revoke or clamp access at any time — `/lockdown` (or
  `bridge-ctl viewonly --all`) drops every editor to view-only instantly.

See [`claude-profiles/README.md`](claude-profiles/README.md) for the profile
capabilities and the full front-desk flow.

## How it's wired (in dotfiles)

When used as the dotfiles submodule, the parent repo's own `scripts/link.sh`
performs the same symlinking, and the service runs the daemon from the stable
`~/.local/bin/agent-bridge` symlink. Profiles resolve relative to this checkout
(`claude-profiles/` beside `bin/`); override with `CLAUDE_PROFILES_DIR`.

## Development

`bin/` mixes Bash and Python; `claude-bridge` is Python 3 (discord.py). Only the
standard library is imported at module level, so the pure helpers (config
loading, message splitting, transcript extraction, signature verification, state
markers, …) are unit-testable without discord.py installed — this repo ships a
**stdlib-only unit-test suite under `tests/`** covering them (the daemon has no
`.py` extension, so the tests load it via `importlib`). Run it with:

```bash
python3 -m unittest discover -s tests
```

CI (`.github/workflows/ci.yml`) runs, on every push and pull request:

- `bash -n` syntax checks over the shell tools,
- **ShellCheck** over the shell tools,
- `py_compile` over `bin/agent-bridge`,
- `jq` validation of every `claude-profiles/*.json`,
- the **`tests/` unit suite** (`python3 -m unittest discover -s tests`),
- **Ruff** error-lint (`E9,F63,F7,F82`) over the daemon and tests — real-error
  rules only, so it never reddens CI over formatting.

Everything that CI checks is self-contained in this repo. Behavioral,
end-to-end tests that exercise a live worker still require the external
`claude-launch` dependency and are not part of this repo's CI.
