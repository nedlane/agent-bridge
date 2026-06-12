---
name: discord-notify
description: Message Ned on Discord from this machine via the discord-notify CLI. Use when finishing a long-running task, hitting a blocker that needs his input, or when he asks to be pinged/notified/messaged on Discord.
---

# Messaging Ned on Discord

This machine has `discord-notify` on PATH. It delivers straight to Ned's
Discord through the local Hermes bot (webhook fallback) — no tokens, no API
calls, safe to use freely.

```sh
discord-notify "ghpr test suite is green, PR ready for review"
discord-notify -t discord:#ops "deploy finished"   # specific channel
git log --oneline -5 | discord-notify -            # body from stdin
```

Targets for `-t`: `discord` (home channel, default), `discord:#channel-name`,
or `discord:<chat_id>[:<thread_id>]`. List live targets with
`hermes send --list discord`.

Guidance:

- Good uses: "done" pings after long tasks, blockers/questions while Ned is
  away, anything he explicitly asked to be notified about.
- Keep it to a few lines — it's a phone notification, not a report. Don't
  paste logs, diffs, or secrets; summarise and say where the detail lives.
- Worker sessions are auto-tagged `worker:<name>`, so don't repeat who you
  are. Task checkboxes already auto-post via a separate hook — don't re-send
  todo lists.
- If it exits non-zero, say so in your final response instead of retrying
  forever.
