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
discord-notify -i coverage.png "coverage went up"       # attach an image/file
discord-notify -i before.png -i after.png "diff"        # several attachments
discord-notify -i build.log                             # attachment, no caption
```

Worker sessions default to their own repo channel automatically; everything
else lands in the main channel. Channel ids for `-t` are in
`~/.config/claude-bridge/config.json`.

Flags: `-t/--to <target>`, `-i/--image <file>` (repeatable; images render
inline in Discord, any other file posts as a download), `-h/--help`.

**Don't run `discord-notify --help` to "see what it does"** — `--help` prints
usage and exits without sending, but everything you need is on this page, so
just use it directly. (Earlier agents spammed the channel by passing `--help`
as a message; that no longer sends, but the reflex still wastes a turn.)

Guidance:

- Good uses: "done" pings after long tasks, blockers/questions while Ned is
  away, anything he explicitly asked to be notified about.
- Keep it to a few lines — it's a phone notification, not a report. Don't
  paste logs, diffs, or secrets; summarise and say where the detail lives.
- Use `-i` for things worth seeing: a screenshot, a rendered chart, a small
  artifact. The message text is optional when an attachment carries the point.
  Don't attach secrets, and don't dump huge files — it's still a phone.
- Worker sessions are auto-tagged `worker:<name>`, so don't repeat who you
  are. Task checkboxes already auto-post via a separate hook — don't re-send
  todo lists.
- If it exits non-zero, say so in your final response instead of retrying
  forever.
