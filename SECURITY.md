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

agent-bridge drives interactive Claude Code **workers** on the host. There are
two trust tiers, and they are deliberately different:

- **Owner-approved workers** (`owner` / `collab` / `utility`) only ever run for
  people you have explicitly authorized — you map the channel and grant the
  access yourself. `owner` and `collab` are **full trust** (they bypass
  permission prompts so a Discord-driven worker never wedges); `utility` is the
  narrow, MCP-only exception. Grant `collab` only to people you'd give a shell.
- **The public `#welcome` greeter** is the only surface any server member can
  reach without your approval, so it is the one that is locked down hard.

Understand these boundaries before exposing the bridge.

### Workers run as the host user

The bridge spawns Claude Code workers inside tmux sessions that run as the
**host Unix user**, with that user's full filesystem and shell access. A worker
is as privileged as the person who started the bridge.

### Capability profiles are a policy jail, not a kernel jail

Each guest channel maps to a worker launched with a capability profile
(`owner` / `collab` / `utility` / `greeter`). Where a profile *does* constrain
the agent (`utility`, `greeter`), it does so through Claude Code's own
permission system (allow/deny lists, tool restrictions) — this is **policy
enforcement inside the agent, not an OS-level sandbox**. `owner` and `collab`
are full trust and impose no such limits at all.

The **`collab`** profile is **full trust — capability-identical to `owner`.**
It adds no restricting flags, so the worker inherits `claude-launch`'s default
`--dangerously-skip-permissions` and **bypasses every permission prompt**. This
is deliberate: a `collab` worker is driven over Discord, where nobody can answer
an interactive "Do you want to proceed?" dialog, so any prompt would wedge the
worker. There is **no deny-list and no sandbox** — a `collab` worker can read
`~/.ssh`, run `sudo`, and touch anything the host user can.

Because `collab` is only ever granted by an explicit owner action (you run
`/addguest`, or approve a request card yourself), **granting it is equivalent
to giving that person shell access as the host user** — grant it only to people
you'd trust with that. Optionally point the channel at a dedicated checkout
(`~/guest-workspaces/<channel>`) to keep a collaborator out of your primary
tree. **Untrusted** users must never receive `collab`; isolate them at the OS
level (separate Unix user or container), which is **out of scope** for this
tool.

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
