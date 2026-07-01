# Capability profiles for guest channels

Each Discord channel maps to a worker launched with one of these **profiles**
(recorded as `repos[<channel_id>].profile` in the bridge config). The bridge's
`profile_args()` turns a profile name into `claude` launch flags, and
`system_prompt()` decides which brief the worker is given.

| Profile   | Who          | What the agent can do |
|-----------|--------------|-----------------------|
| `owner`   | You          | Everything. Full trust, primary tree. Default; adds no flags. |
| `utility` | any guest    | **Only** the profile's MCP server(s). No built-in tools at all. |
| `collab`  | collaborator | Normal dev tools, confined to a dedicated checkout, with deny guardrails. |
| `greeter` | public       | Front-desk only: converse in `#welcome` and file access requests. |

> **These profiles require `claude-launch`.** The flags below
> (`--enforce-perms`, `--tools`, `--mcp-config`, `--strict-mcp-config`,
> `--allowedTools`, `--settings`) are understood by the `claude-launch` wrapper,
> not by stock `claude`. In particular `--enforce-perms` turns **off**
> `claude-launch`'s default `--dangerously-skip-permissions`, so the settings
> allow/deny rules actually take effect. See the top-level README's
> "About `claude-launch`" section.

## ⚠️ You MUST rewrite the deny-list paths before enabling a guest profile

The `deny` rules in `collab.settings.json` and `greeter.settings.json` are
**absolute paths into the original maintainer's home directory**, e.g.:

```json
"Read(//home/nedlane/.ssh/**)"
```

The leading `//` is Claude Code's convention for anchoring a permission rule to
an **absolute filesystem path** (rather than a path interpreted relative to the
worker's working directory). Those paths point at `/home/nedlane/...`.

**If you clone this and don't change them, the deny rules match nothing on your
machine.** They silently become a no-op, and the (already policy-only)
guardrails protect nothing. Before you enable a `collab` or `greeter` guest,
rewrite every `//home/nedlane/...` path to your own `$HOME` (keep the leading
`//`). Double-check with `jq '.permissions.deny' collab.settings.json` that the
paths name *your* `.ssh`, credential stores, `~/.claude`, and any tree you want
off-limits.

## `owner`

Full trust, no extra flags — the default for your own channels. The worker still
gets the Discord protocol injected via `--append-system-prompt`, but nothing is
restricted.

## `utility` (e.g. a calendar agent)

Launched with `--tools ""` (no built-in tools at all) + `--mcp-config
utility.mcp.json --strict-mcp-config` (only the MCP servers named in that file)
+ `--allowedTools mcp__gcal` (its tools run pre-approved, since a guest can't
answer TUI permission dialogs over Discord). `utility.settings.json` adds a
deny-list as defense in depth. The result is an assistant with exactly one
capability and nowhere to wander.

**To finish the calendar agent:** fill `utility.mcp.json` with a Google Calendar
MCP server named `gcal` and its OAuth credentials. Both `utility.mcp.json` and
`utility.settings.json` are placeholders today, so until then a `utility` worker
has no tools.

## `collab` (a shared repo)

Runs in a **dedicated checkout** (convention: `~/guest-workspaces/<channel>`),
not your primary tree — that separate checkout is the real containment.
`collab.settings.json` allows the normal dev tools (`Read`, `Edit`, `Write`,
`Glob`, `Grep`, `Bash`) with `defaultMode: acceptEdits` so the guest isn't
blocked on prompts they can't answer, while denying `sudo`/`su`, network fetches
(`curl`, `wget`), and reads/edits of `~/.ssh`, `~/.aws`, `gh` config, the
`claude-workers`/`claude-bridge` config dirs, `~/.claude`, and the dotfiles tree.

Be honest about what this is:

- It is a **policy jail, not a kernel jail** — a deliberate trade-off for
  *trusted* collaborators (guarding against accidents, not attackers).
- `collab` **grants `Bash`**, so the deny-list is a policy layer on top of a
  general-purpose shell, **not a hard credential boundary.** A determined user
  with Bash has many ways around a pattern-matched deny-list. Treat it as
  guardrails, not a sandbox.
- For **untrusted** guests, move to OS-level isolation — a separate Unix user or
  a container. That's out of scope for these profiles.
- If you point a `collab` channel at a directory that is *not* under
  `~/guest-workspaces`, the bridge warns when you add an editing guest, because
  that guest's dev tools then run against a real tree.

## `greeter` (the public front desk)

The `greeter` profile powers a **public** `#welcome` channel (config
`welcome_channel`) that any server member can talk to. Unlike the owner/collab
workers, it is not a push-first Ned worker: it converses, and whatever it writes
is posted straight back into `#welcome`. It gets the `GREETER` brief (from
`system_prompt()`) instead of the normal Discord protocol.

Its job is narrow — help a visitor request access to a project — and it is
locked down hard (see `profile_args()`'s `greeter` branch):

- `--mcp-config greeter.mcp.json --strict-mcp-config` with an **empty**
  `mcpServers` — it loads no MCP tools at all.
- `greeter.settings.json` allows **only two `bridge-ctl` verbs** and denies
  everything else:
  - `bridge-ctl repos` — to read the real project names (it must never invent
    projects).
  - `bridge-ctl request <discord_id> <project> "<summary>"` — to file an access
    request.
  - `Edit`, `Write`, `WebFetch`, `WebSearch`, `Task`, and network/remote Bash
    (`curl`, `wget`, `nc`, `ssh`, …) are explicitly denied, and reads of
    `~/.ssh`, `~/.config`, `~/.claude`, and the dotfiles tree are blocked.

Because everything else stalls on a permission prompt nobody can answer, a
stranger can't coax the greeter into acting beyond those two commands.

### The request → approval flow

1. A visitor talks to the greeter in `#welcome`. The greeter matches their ask
   to a real project (via `bridge-ctl repos`) and runs `bridge-ctl request
   <their-id> <project> "<one-line summary>"`.
2. That posts an **approval card** to the requests channel (config
   `requests_channel`), pre-seeded with three reactions:
   👁️ view-only · ✏️ edit · ❌ deny. The card embeds a `req:<id>:<project>`
   marker so the decision survives a bridge restart (state lives in the message,
   not memory).
3. The **owner** reacts on the card (`handle_request_reaction`):
   - ✏️ or 👁️ → `do_addguest` sets a per-member Discord permission overwrite on
     the project's channel (View+Send for edit, View-only for view) and records
     the guest in `guests` / `viewers`. Granted guests join through a `collab`
     profile.
   - ❌ → denied; the visitor is told, and no access is granted.
   Either way the greeter posts the outcome back to the visitor in `#welcome`,
   and the card's marker is stripped so it can't re-fire.

The owner can walk any of this back later with `bridge-ctl revoke`,
`bridge-ctl viewonly <name>`, or `/lockdown` (drop every editor to view-only
everywhere).
