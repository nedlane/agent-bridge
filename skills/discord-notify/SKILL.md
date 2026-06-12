---
name: discord-notify
description: Message Ned on Discord from this machine via the discord-notify CLI. Use when finishing a long-running task, hitting a blocker that needs his input, or when he asks to be pinged/notified/messaged on Discord.
---

# Messaging Ned on Discord

This machine has `discord-notify` on PATH. It delivers straight to Ned's
Discord through the local claude-bridge (webhook fallback) — no model calls,
safe to use freely.

```sh
discord-notify "ghpr test suite is green, PR ready for review"
discord-notify -t discord:1234567890 "deploy finished"  # specific channel id
git log --oneline -5 | discord-notify -                 # body from stdin
```

Worker sessions default to their own repo channel automatically; everything
else lands in the main channel. Channel ids for `-t` are in
`~/.config/claude-bridge/config.json`.

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
