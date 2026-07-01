# Security Policy

## Reporting a Vulnerability

Please report security issues **privately** — do not open a public issue for
anything that could be a vulnerability.

Preferred: open a private **GitHub Security Advisory** via the repository's
**Security** tab ("Report a vulnerability"). If that is unavailable to you,
contact the maintainer privately through GitHub instead of filing a public
issue.

Please include enough detail to reproduce (affected component, configuration,
and steps). You will get an acknowledgement and, where appropriate, a
coordinated disclosure once a fix is available.

## Security model & limitations

agent-bridge drives interactive Claude Code **workers** on the host. Understand
these trust boundaries before exposing it to anyone you don't trust.

### Workers run as the host user

The bridge spawns Claude Code workers inside tmux sessions that run as the
**host Unix user**, with that user's full filesystem and shell access. A worker
is as privileged as the person who started the bridge.

### Capability profiles are a policy jail, not a kernel jail

Each guest channel maps to a worker launched with a capability profile
(`owner` / `collab` / `utility` / `greeter`). These profiles constrain the
agent through Claude Code's own permission system (allow/deny lists, tool
restrictions) — they are **policy enforcement inside the agent, not an
OS-level sandbox**.

In particular, the **`collab`** profile grants the **Bash** tool. Its
`Read`/`Edit` path deny-lists (e.g. `~/.ssh`, `~/.aws`, `~/.config/gh`,
`~/.config/claude-workers`, `~/.claude`, the dotfiles tree) and its
command-prefix denies (`sudo`, `su`, `curl`, `wget`) raise the bar but do
**not** stop a determined guest: arbitrary Bash can read files and exfiltrate
data through countless paths those prefix/path filters don't cover. The
`collab` worker also runs in a dedicated checkout
(`~/guest-workspaces/<channel>`) rather than the owner's primary tree, which is
the real containment for accidents.

Treat `collab` guests as **trusted collaborators** — the model defends against
accidents, not attackers. **Untrusted** guests require OS-level isolation
(a separate Unix user or a container), which is **out of scope** for this tool.

### The public `#welcome` greeter is a soft boundary

The `#welcome` greeter is reachable by **any** Discord user in the server. It
loads no MCP servers (`--strict-mcp-config` with an empty config) and is
confined by its system prompt plus a narrow Bash allow-list (only
`bridge-ctl repos` and `bridge-ctl request:*`). This is
**defense-in-depth, not a hard security boundary** — prompt-level confinement
can be probed, so do not rely on it alone to protect anything sensitive.

### Control plane: signed, localhost-only, no replay protection

Control events (the Stop hook, relays, and `bridge-ctl`) are delivered over an
HTTP listener bound to **`127.0.0.1` only**. Requests are authenticated with an
**HMAC-SHA256** signature (`X-Webhook-Signature`) over the request body, using
a **shared secret** read from a `chmod 600` file. Anyone who can read that
secret can **forge valid control events**. There is currently **no
replay/nonce protection** on the listener; the mitigations are the
localhost-only bind and the secret-file permissions.

### Secrets

Secret material (Discord bot token, webhook URL/secret) lives under
`~/.config/claude-workers/` and **must be `chmod 600`**. These files are never
committed to the repository. Keep their permissions tight — reading the webhook
secret is equivalent to being able to command the bridge.
