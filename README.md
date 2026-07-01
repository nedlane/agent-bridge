# agent-bridge

A Discord-driven control plane for interactive Claude Code workers.

`claude-bridge` maps each Discord channel (under a **Claude** category) 1:1 to a
persistent tmux Claude Code worker, with hook-driven reply/checklist relays,
per-channel capability profiles (owner / collab / utility / greeter), and a
readiness check. It is desktop-only tooling extracted from
[nedlane/dotfiles](https://github.com/nedlane/dotfiles), where it lives as a
git submodule under `hosts/wsl-desktop/agent-bridge/`.

## Layout

| Path | What |
|---|---|
| `bin/claude-bridge` | Python daemon: Discord ↔ worker pipe (discord.py + signed-event HTTP listener) |
| `bin/claude-worker` | Worker lifecycle over tmux sessions (`cw-<name>`) |
| `bin/bridge-ctl` | Thin signed client: add repos/guests, request approvals |
| `bin/discord-notify` | Post a message/file to a Discord channel from the host |
| `bin/claude-worker-todo-relay` | PostToolUse/TodoWrite hook → live task checkboxes to Discord |
| `bin/claude-worker-done-relay` | Stop hook → completion push to Discord |
| `bin/agent-checkup` | Readiness / auth-mode report |
| `bin/term-shot` | Captured terminal → text/image |
| `claude-profiles/` | Per-channel capability profiles (`*.settings.json`, `*.mcp.json`) |
| `skills/` | Claude Code skills: `claude-bridge`, `discord-notify` |
| `systemd/claude-bridge.service` | User service for the daemon |

## How it's wired (in dotfiles)

`scripts/link.sh` in the parent dotfiles repo symlinks `bin/*` into
`~/.local/bin`, `skills/*` into `~/.claude/skills`, and the systemd unit into
`~/.config/systemd/user`. The service runs the daemon from the stable
`~/.local/bin/claude-bridge` symlink.

## Configuration

- Profiles resolve relative to this checkout (`claude-profiles/` beside `bin/`);
  override with `CLAUDE_PROFILES_DIR`.
- `claude-worker` locates the shared `claude-launch` via `$DOTFILES_DIR`/`PATH`
  (it lives in the parent dotfiles repo, not here).

## Development

`bin/` mixes Bash and Python; `claude-bridge` is Python 3 (discord.py). CI runs
ShellCheck over the shell tools, `py_compile` over the daemon, and validates the
profile JSON. Behavioral smoke tests live in the dotfiles repo (`tests/`), where
the shared `claude-launch` dependency is present.
