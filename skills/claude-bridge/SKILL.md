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
bridge-ctl repos                        # list channel -> repo mappings
discord-notify -t discord:<channel_id> "first message"   # talk into it
```

Ned can also type `!addrepo <name> <path>` in Discord. Prefer these tools
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

In Discord: `!status`, `!stop [name]`, `!restart [name]`, `!screen [name]`,
`!model <model> [name]`, `!clear [name]` (fresh context via restart without
`--continue`), `!compact [name]` — and any `/slash-command` message is typed
straight into the worker.

Common failures: worker replies not arriving → check the Stop hook chain
(`~/.claude/settings.json` registers `claude-worker-done-relay`;
`~/.config/claude-workers/bridge-webhook` must hold the listener URL +
secret matching the bridge). Messages ignored → sender not in
`allowed_users`, or channel not in `repos`.
