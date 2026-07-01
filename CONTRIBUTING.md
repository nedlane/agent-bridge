# Contributing

Thanks for your interest in improving agent-bridge. This is desktop tooling
that maps Discord channels to interactive Claude Code workers; the notes below
cover local development.

## Prerequisites

- **Python 3** and **tmux** — required for the tooling in general.
- **discord.py** and **Pillow** — only needed to actually *run* the daemon
  (`bin/claude-bridge`). You do **not** need them to run the tests: the pure
  helpers are importable with the standard library alone.

## Running CI locally

CI is intentionally lightweight. You can reproduce it from the repo root:

```sh
# 1. Bash syntax + ShellCheck on the shell tools in bin/ (skip Python shebangs)
for f in bin/*; do
  head -1 "$f" | grep -qE 'zsh|python' && continue
  bash -n "$f"
  shellcheck "$f"
done

# 2. Byte-compile the Python daemon
python3 -m py_compile bin/claude-bridge

# 3. Validate the capability-profile JSON
for f in claude-profiles/*.json; do
  jq empty "$f"
done

# 4. Run the stdlib-only unit tests for the pure helpers
python3 -m unittest discover -s tests
```

## Code style

- **Shell** must pass `shellcheck` cleanly.
- **Python** stays **standard-library-only at module level**, so the pure
  helper functions remain importable without `discord.py` installed. Keep any
  `discord.py` / `Pillow` imports out of module top-level import paths that the
  helpers depend on.

## Keeping the slash-command list in sync

When you add, remove, or rename a slash command, update **all three** places so
they don't drift:

1. The command list in the `bin/claude-bridge` module docstring.
2. The `claude-bridge` skill (under `skills/`).
3. The `README`.

Thanks for keeping things consistent!
