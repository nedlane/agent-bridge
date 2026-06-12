---
name: claude-bridge
description: Manage the Discord↔Claude-worker bridge on this machine — add a repo channel, check/restart the bridge service, debug why Discord messages aren't reaching workers. Use when Ned asks to hook a repo up to Discord, set up a channel for a repo, or fix the bridge.
---

# claude-bridge

`claude-bridge` (systemd user service) is a deterministic pipe: each Discord
channel under the **Claude** category maps to one tmux Claude Code worker
(`claude-worker`). No LLM in the bridge. Messages in `#<repo>` go to the
worker; the worker's replies come back via its Stop hook; idle workers are
reaped and revived with `claude --continue`.

## Add a repo channel

Preferred: Ned types `!addrepo <name> <path>` in any channel the bot can
see — the bridge creates `#<name>` under the Claude category and saves the
mapping itself.

From this machine instead: edit `~/.config/claude-bridge/config.json` and add
`"<channel_id>": {"name": "<worker>", "dir": "<abs path>"}` under `repos`,
then `systemctl --user restart claude-bridge`. To also create the Discord
channel from here, POST to the Discord API using the bot token from
`~/.config/claude-workers/discord-bot-token` (never print it):

```sh
curl -fsS -X POST "https://discord.com/api/v10/guilds/<guild_id>/channels" \
  -H "Authorization: Bot $(cat ~/.config/claude-workers/discord-bot-token)" \
  -H 'Content-Type: application/json' \
  -d '{"name": "<repo>", "type": 0, "parent_id": "1515015058009231370"}'
```

## Operate / debug

```sh
systemctl --user status claude-bridge      # running?
journalctl --user -u claude-bridge -n 50   # bridge logs
ss -tln | grep 8765                        # event listener up?
agent-checkup                              # full readiness report
```

In Discord: `!status`, `!stop [name]`, `!restart [name]`, `!screen [name]`.

Common failures: worker replies not arriving → check the Stop hook chain
(`~/.claude/settings.json` registers `claude-worker-done-relay`;
`~/.config/claude-workers/bridge-webhook` must hold the listener URL +
secret matching the bridge). Messages ignored → sender not in
`allowed_users`, or channel not in `repos`.
