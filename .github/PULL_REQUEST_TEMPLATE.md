## Summary

<!-- One or two sentences: what does this PR do and why? -->

## What changed

<!-- Bullet the concrete changes (files, behavior, commands). -->

-

## How tested

<!-- Which of the local CI steps did you run? (see CONTRIBUTING.md) -->

- [ ] `bash -n` + `shellcheck` on the shell tools in `bin/`
- [ ] `python3 -m py_compile bin/claude-bridge`
- [ ] `jq empty claude-profiles/*.json`
- [ ] `python3 -m unittest discover -s tests`

## Checklist

- [ ] ShellCheck passes on all shell tools
- [ ] `py_compile` passes on `bin/claude-bridge`
- [ ] Unit tests pass
- [ ] If a slash command was added/removed/renamed, the module docstring, the
      `claude-bridge` skill, and the README were all updated
