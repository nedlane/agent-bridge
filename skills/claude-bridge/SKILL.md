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

## Add a repo channel (create a new chat)

From any Claude session or shell on this machine — workers included:

```sh
bridge-ctl addrepo <name> </abs/path>   # creates #<name>, maps it, returns
                                        # {"channel_id": ...}
bridge-ctl start <name>                 # start that repo's worker via the
                                        # bridge (protocol injected, resumes)
bridge-ctl peek <name>                  # post that worker's live screen into
                                        # its channel (like the 👀 reaction)
bridge-ctl repos                        # list channel -> repo mappings
discord-notify -t discord:<channel_id> "first message"   # talk into it
```

Ned can also run the `/addrepo <name> <path>` slash command in Discord. Prefer these tools
over editing config or calling the Discord REST API by hand; the bridge owns
channel creation and the mapping file. To hand a worker a task end-to-end:
`bridge-ctl start <name>`, then `claude-worker send <name> "the task"`.

## The orchestrator

The worker named `orchestrator` (channel `#orchestrator`) is Ned's
natural-language remote for all of this — the bridge injects an extra brief
teaching it the tools above, so "spin up a worker on ghpr and have it triage
the failing tests" works without bot commands. It is an ordinary worker
otherwise (idle-reaped, resumed with `--continue`).

## Operate / debug

```sh
systemctl --user status claude-bridge      # running?
journalctl --user -u claude-bridge -n 50   # bridge logs
ss -tln | grep 8765                        # event listener up?
agent-checkup                              # full readiness report
```

In Discord these are native slash commands (synced per-guild on connect; the
`worker` option defaults to the channel's worker): `/status`, `/stop`,
`/restart`, `/screen`, `/model <model>`, `/clear` (fresh context via restart
without `--continue`), `/fresh` (shut down + fresh next start, no resume),
`/compact`, `/checkin`, `/addrepo <name> <path>`, `/close`. `/clear` and
`/fresh` also purge the channel's messages (Ned's and the bot's) to clear
clutter — this needs the bot's **Manage Messages** permission in the Claude
category. `/close [worker] confirm:<name>` fully retires a worker — stops it,
wipes its saved state, and **deletes its channel** (irreversible; you must
retype the worker name in `confirm` to proceed, and the bot needs **Manage
Channels**). Guest access has its own commands: `/addguest <name> <discord_id>
[edit|view]` grants a guest edit or view access to one channel, and
`/lockdown` drops every guest everywhere to view-only. Any *other*
`/slash-command` message (not one of these) is typed straight into the worker.
The bot must be invited with the `applications.commands` scope for these to
register.

Common failures: worker replies not arriving → check the Stop hook chain
(`~/.claude/settings.json` registers `claude-worker-done-relay`;
`~/.config/claude-workers/bridge-webhook` must hold the listener URL +
secret matching the bridge). Messages ignored → sender not in
`allowed_users`, or channel not in `repos`.
