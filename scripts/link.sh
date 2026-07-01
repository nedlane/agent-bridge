#!/usr/bin/env bash
# link.sh — symlink this repo's tools into place for a standalone
# (non-dotfiles) install.
#
# Links:
#   bin/*                 -> ~/.local/bin/
#   skills/<dir>          -> ~/.claude/skills/<dir>
#   systemd/*.service     -> ~/.config/systemd/user/
#
# Only manages symlinks; never overwrites a real file. Idempotent — safe to
# re-run. Next steps are printed at the end.
set -euo pipefail

# Resolve this script's own location so it works from any cwd; REPO_ROOT is the
# parent of the scripts/ directory that holds this file.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# ~/.local/bin and ~/.claude are conventional, so use them directly; respect
# XDG for config (systemd user units) and data locations.
BIN_DIR="$HOME/.local/bin"
SKILLS_DIR="$HOME/.claude/skills"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
SYSTEMD_USER_DIR="$CONFIG_HOME/systemd/user"
# XDG data dir, exported so it is honoured by anything this script may call.
export XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"

linked=0

# link SRC DEST — create/refresh a symlink at DEST pointing to SRC, but refuse
# to clobber a real (non-symlink) file or directory already sitting there.
link() {
  local src="$1" dest="$2"
  if [[ -e "$dest" && ! -L "$dest" ]]; then
    printf '  skip  %s (real file/dir in the way, not touching it)\n' "$dest"
    return 0
  fi
  ln -sfn "$src" "$dest"
  printf '  link  %s -> %s\n' "$dest" "$src"
  linked=$((linked + 1))
}

echo "== linking agent-bridge from $REPO_ROOT =="

# --- bin/ -> ~/.local/bin ------------------------------------------------------
if [[ -d "$REPO_ROOT/bin" ]]; then
  mkdir -p "$BIN_DIR"
  for src in "$REPO_ROOT"/bin/*; do
    [[ -e "$src" ]] || continue
    link "$src" "$BIN_DIR/$(basename "$src")"
  done
fi

# --- skills/<dir> -> ~/.claude/skills/<dir> -----------------------------------
if [[ -d "$REPO_ROOT/skills" ]]; then
  mkdir -p "$SKILLS_DIR"
  for src in "$REPO_ROOT"/skills/*/; do
    [[ -d "$src" ]] || continue
    src="${src%/}"
    link "$src" "$SKILLS_DIR/$(basename "$src")"
  done
fi

# --- systemd/*.service -> ~/.config/systemd/user ------------------------------
if [[ -d "$REPO_ROOT/systemd" ]]; then
  mkdir -p "$SYSTEMD_USER_DIR"
  for src in "$REPO_ROOT"/systemd/*.service; do
    [[ -e "$src" ]] || continue
    link "$src" "$SYSTEMD_USER_DIR/$(basename "$src")"
  done
fi

echo "== linked $linked item(s) =="

cat <<'NEXT'

Next steps:
  1. Create the bridge config + secret files (see README.md).
  2. Register the Claude Code hooks (see docs/agent-control-plane.md).
  3. Enable the service:
       systemctl --user daemon-reload
       systemctl --user enable --now claude-bridge
  4. Verify:
       agent-checkup
NEXT
