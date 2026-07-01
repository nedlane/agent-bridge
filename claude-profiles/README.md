# Capability profiles for guest channels

Each Discord guest channel maps to a worker launched with one of these
**profiles** (recorded as `repos[channel].profile` in the bridge config). The
bridge's `profile_args()` turns a profile into `claude` launch flags. See
`docs/superpowers/specs/2026-07-02-guest-channels-design.md`.

| Profile   | Who         | What the agent can do |
|-----------|-------------|-----------------------|
| `owner`   | Ned         | Everything. Full trust, primary tree. Default; adds no flags. |
| `utility` | any guest   | **Only** the profile's MCP server(s). No built-in tools at all. |
| `collab`  | collaborator| Normal dev tools, confined to a dedicated checkout, with deny guardrails. |

## `utility` (e.g. the calendar agent)

Launched with `--tools ""` (no built-in tools) + `--mcp-config utility.mcp.json
--strict-mcp-config` (only these MCP servers) + `--allowedTools mcp__gcal` (its
tools run without a permission prompt — a guest can't answer TUI dialogs).
`utility.settings.json` adds a deny-list as defense in depth.

**To finish the calendar agent:** fill `utility.mcp.json` with a Google
Calendar MCP server named `gcal` and its OAuth credentials. Until then a
utility worker has no tools.

## `collab` (a shared repo)

Runs in a **dedicated checkout** (`~/guest-workspaces/<channel>`), not Ned's
primary tree — that checkout is the real containment. `collab.settings.json`
auto-accepts dev tools (so the guest isn't blocked on prompts they can't
answer) while denying `sudo`, network fetches, and reads/edits of `~/.ssh`,
credentials, `~/.claude`, and the dotfiles tree.

This is a **policy jail, not a kernel jail** (Ned's chosen trade-off for
trusted collaborators — accidents, not attackers). For untrusted guests, move
to OS-level isolation (separate Unix user / container) — out of scope today.

Paths in the deny-lists are absolute for this machine (`//home/nedlane/...`).
