# Distributed agent-bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `agent-bridge` run first-class Claude/Codex workers on satellite tailnet machines (mac, archbtw) driven from the same Discord, with full parity (send, colour peek, lifecycle, replies, todos, attachments).

**Architecture:** The parent bridge on dev-box stays the sole Discord owner. A repo mapping gains a `host`; every worker action (already funnelled through the `agent-worker` CLI via `_run`) is dispatched locally (`subprocess`) or to the worker's host over `ssh`. Workers' return traffic (`discord-notify`, done/todo relays) reaches the parent's event listener over the tailnet, HMAC-secured. Reply extraction moves into the relay so no transcript is read across the network.

**Tech Stack:** Python 3 (`bin/agent-bridge`, stdlib only — discord.py/aiohttp are lazy-imported), Bash (`bin/agent-worker`, `bin/claude-worker-*-relay`, `bin/discord-notify`), tmux, tailscale SSH. Tests: stdlib `unittest` loaded via `importlib` (`python3 -m unittest discover -s tests -v`).

## Global Constraints

- **Byte-identical local behavior by default.** With no `machines`, every repo `host` defaulting to `local`, and `listen_host` absent (→ `127.0.0.1`), behavior must match today exactly. Every task asserts this.
- **Stdlib-only tests.** Load `bin/agent-bridge` with `importlib.SourceFileLoader` as `tests/test_claude_bridge.py` does; no discord.py/aiohttp import at test time (keep new logic in pure helpers, not inside `run_bridge`).
- **No new pip dependencies.**
- **Preserve HMAC auth** on every `/event` POST (`X-Webhook-Signature`, `BRIDGE_WEBHOOK_SECRET`). Do not weaken it when leaving loopback.
- **All Discord egress stays on the single bot** (no per-channel webhooks added on satellites).
- **Dev-box tailnet IP:** `100.105.249.62`. **Machines:** `mac → christiannucifora@hc-002`, `archbtw → archbtw`.
- Run the full suite after each task: `python3 -m unittest discover -s tests -v`.

---

### Task 1: Host resolution helper

**Files:**
- Modify: `bin/agent-bridge` (add `resolve_host_target` near the other config helpers, after `channel_allows`)
- Test: `tests/test_claude_bridge.py`

**Interfaces:**
- Consumes: `cfg` dict (may contain `"machines"`), a repo dict (may contain `"host"`).
- Produces: `resolve_host_target(cfg, repo) -> str | None` — returns the SSH target string for a remote repo, or `None` for a local repo (host absent or `"local"`). Raises `KeyError` with a clear message if `host` names a machine missing from `cfg["machines"]`.

- [ ] **Step 1: Write the failing test**

```python
class ResolveHostTargetTests(unittest.TestCase):
    def test_absent_host_is_local(self):
        self.assertIsNone(cb.resolve_host_target({}, {"name": "a"}))

    def test_explicit_local_is_local(self):
        self.assertIsNone(cb.resolve_host_target({}, {"host": "local"}))

    def test_remote_host_resolves_ssh_target(self):
        cfg = {"machines": {"mac": {"ssh": "me@hc-002"}}}
        self.assertEqual(cb.resolve_host_target(cfg, {"host": "mac"}), "me@hc-002")

    def test_unknown_machine_raises(self):
        with self.assertRaises(KeyError):
            cb.resolve_host_target({"machines": {}}, {"host": "mac"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_claude_bridge -v -k ResolveHostTarget`
Expected: FAIL — `module 'claude_bridge' has no attribute 'resolve_host_target'`

- [ ] **Step 3: Write minimal implementation**

```python
def resolve_host_target(cfg, repo):
    """SSH target for a repo's worker, or None when it runs locally.

    A repo with no `host` (or host == "local") runs on this machine, exactly as
    before. Any other host must name an entry in cfg["machines"] whose "ssh"
    field is the target this bridge SSHes to.
    """
    host = (repo or {}).get("host") or "local"
    if host == "local":
        return None
    machines = cfg.get("machines") or {}
    if host not in machines:
        raise KeyError(f"repo host '{host}' has no entry in config 'machines'")
    return machines[host]["ssh"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_claude_bridge -v -k ResolveHostTarget`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/agent-bridge tests/test_claude_bridge.py
git commit -m "bridge: resolve a repo's worker host to an SSH target"
```

---

### Task 2: SSH argv wrapper

**Files:**
- Modify: `bin/agent-bridge` (add `ssh_wrap` next to `_run`)
- Test: `tests/test_claude_bridge.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `ssh_wrap(target, argv, env=None) -> list[str]` — wraps an `agent-worker` argv to run on `target` over SSH, forwarding `env` (dict) as an explicit `env NAME=VAL` prefix (SSH does not carry the caller's env). Every argument is shell-quoted so text like a system prompt survives.

- [ ] **Step 1: Write the failing test**

```python
import shlex

class SshWrapTests(unittest.TestCase):
    def test_wraps_with_ssh_and_quotes(self):
        out = cb.ssh_wrap("me@hc-002", ["agent-worker", "read", "app", "40"])
        self.assertEqual(out[:3], ["ssh", "me@hc-002", "--"])
        # remainder is one shell-quoted string safe to hand to the remote shell
        self.assertIn("agent-worker read app 40", " ".join(out[3:]))

    def test_forwards_env_prefix(self):
        out = cb.ssh_wrap("h", ["agent-worker", "send", "a", "hi there"],
                          env={"CLAUDE_WORKER": "a"})
        remote = out[3]
        self.assertTrue(remote.startswith("env CLAUDE_WORKER=a "))
        # embedded spaces/quotes in the message are preserved through quoting
        self.assertIn(shlex.quote("hi there"), remote)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_claude_bridge -v -k SshWrap`
Expected: FAIL — no attribute `ssh_wrap`

- [ ] **Step 3: Write minimal implementation**

```python
def ssh_wrap(target, argv, env=None):
    """argv to run `argv` (an agent-worker command) on `target` over SSH.

    SSH runs its command string in a login shell, so we build ONE shell-safe
    string (each token shlex-quoted) and forward any needed env explicitly as an
    `env NAME=VAL` prefix — the caller's environment does not cross SSH.
    """
    assignments = "".join(
        f"{shlex.quote(k)}={shlex.quote(str(v))} " for k, v in (env or {}).items()
    )
    prefix = f"env {assignments}" if env else ""
    remote = prefix + " ".join(shlex.quote(a) for a in argv)
    return ["ssh", target, "--", remote]
```

Note: ensure `import shlex` is present at the top of `bin/agent-bridge` (add it if missing).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_claude_bridge -v -k SshWrap`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/agent-bridge tests/test_claude_bridge.py
git commit -m "bridge: build a shell-safe SSH wrapper for agent-worker commands"
```

---

### Task 3: Host-aware dispatch through `run_worker_cmd`, wired into the worker_* helpers

**Files:**
- Modify: `bin/agent-bridge` — add `run_worker_cmd`; give `worker_start/worker_send/worker_key/worker_screen/worker_stop` a `host_target` parameter; update their call sites to pass the resolved target.
- Test: `tests/test_claude_bridge.py`

**Interfaces:**
- Consumes: `_run` (existing), `ssh_wrap` (Task 2), `resolve_host_target` (Task 1).
- Produces: `run_worker_cmd(host_target, argv, timeout=180, input_text=None, env=None) -> subprocess.CompletedProcess`. `host_target is None` → `_run(argv)` unchanged; else `_run(ssh_wrap(host_target, argv, env))`. The worker_* helpers gain a trailing `host_target=None` kwarg and route through it.

- [ ] **Step 1: Write the failing test** (assert local path is byte-identical and remote path is SSH-wrapped, by capturing what argv reaches `_run`)

```python
class RunWorkerCmdTests(unittest.TestCase):
    def setUp(self):
        self.seen = {}
        self._orig = cb._run
        cb._run = lambda args, timeout=180, input_text=None: self.seen.setdefault("args", args) or \
            __import__("subprocess").CompletedProcess(args, 0, "", "")

    def tearDown(self):
        cb._run = self._orig

    def test_local_passes_argv_unchanged(self):
        cb.run_worker_cmd(None, ["agent-worker", "stop", "a"])
        self.assertEqual(self.seen["args"], ["agent-worker", "stop", "a"])

    def test_remote_wraps_with_ssh(self):
        cb.run_worker_cmd("me@h", ["agent-worker", "stop", "a"])
        self.assertEqual(self.seen["args"][:3], ["ssh", "me@h", "--"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_claude_bridge -v -k RunWorkerCmd`
Expected: FAIL — no attribute `run_worker_cmd`

- [ ] **Step 3: Write minimal implementation** (add the function, then rewire the helpers)

```python
def run_worker_cmd(host_target, argv, timeout=180, input_text=None, env=None):
    """Dispatch an agent-worker command to its host. host_target None → local
    (byte-identical to today); otherwise wrap for SSH."""
    if host_target is None:
        return _run(argv, timeout=timeout, input_text=input_text)
    return _run(ssh_wrap(host_target, argv, env=env), timeout=timeout,
                input_text=input_text)
```

Then update each helper to accept and forward `host_target`. Example for the ones that currently call `_run([...])` directly:

```python
def worker_send(name, text, typed=False, host_target=None):
    args = ["agent-worker", "send", name]
    if typed:
        args.append("--type")
    return run_worker_cmd(host_target, args + [text], timeout=30)

def worker_screen(name, lines=40, ansi=False, host_target=None):
    args = ["agent-worker", "read", name, str(lines)]
    if ansi:
        args.append("--ansi")
    return run_worker_cmd(host_target, args, timeout=15)

def worker_key(name, *keys, host_target=None):
    return run_worker_cmd(host_target, ["agent-worker", "key", name, *keys], timeout=15)

def worker_stop(name, host_target=None):
    return run_worker_cmd(host_target, ["agent-worker", "stop", name], timeout=30)

def worker_start(name, directory, channel_id, resume, profile="owner",
                 harness="claude", host_target=None):
    return run_worker_cmd(
        host_target,
        start_args(name, directory, channel_id, resume, profile, harness),
        timeout=180,
    )
```

- [ ] **Step 4: Run test to verify it passes, and the whole suite (backward-compat)**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS (existing callers pass no `host_target`, so `None` → unchanged behavior)

- [ ] **Step 5: Wire call sites to pass the resolved target**

In `run_bridge`, wherever a worker helper is called with a known `repo`, compute the target once and pass it. Pattern (apply at each call site — start, send/deliver, screen/peek, key, stop, and the wedge-recovery restart):

```python
target = resolve_host_target(cfg, repo)   # repo is the mapping dict already in scope
await asyncio.to_thread(worker_screen, name, 40, True, host_target=target)
```

For sites that only have `name` (not `repo`), add a small `repo_for(name)` lookup (mirror the existing `repo_by_name`) and resolve from it.

- [ ] **Step 6: Run the suite again and commit**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS

```bash
git add bin/agent-bridge tests/test_claude_bridge.py
git commit -m "bridge: dispatch worker commands to their host (local or SSH)"
```

---

### Task 4: Configurable listener bind (`listen_host`)

**Files:**
- Modify: `bin/agent-bridge` — default in the config loader (near the `listen_port` default at line ~467) and the `web.TCPSite(...)` construction (line ~3770).
- Test: `tests/test_claude_bridge.py`

**Interfaces:**
- Produces: `cfg["listen_host"]` defaulting to `"127.0.0.1"`.

- [ ] **Step 1: Write the failing test** (config default) — find the pure config-normalizing helper the loader uses (the one that sets `allowed_users: []`, `listen_port` defaults). Test that it fills `listen_host`.

```python
class ListenHostDefaultTests(unittest.TestCase):
    def test_defaults_to_loopback(self):
        cfg = cb.normalize_config({})      # use the actual loader/normalizer name
        self.assertEqual(cfg["listen_host"], "127.0.0.1")

    def test_respects_explicit_value(self):
        cfg = cb.normalize_config({"listen_host": "100.105.249.62"})
        self.assertEqual(cfg["listen_host"], "100.105.249.62")
```

(If config defaults are applied inline in `run_bridge` rather than a helper, first extract them into a `normalize_config(raw) -> cfg` pure function, then test that — this also makes the existing `listen_port`/`allowed_users` defaults testable.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_claude_bridge -v -k ListenHost`
Expected: FAIL

- [ ] **Step 3: Implement the default and use it at bind time**

```python
# in the config normalizer:
cfg.setdefault("listen_host", "127.0.0.1")
# at bind:
site = web.TCPSite(runner, cfg["listen_host"], int(cfg["listen_port"]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_claude_bridge -v -k ListenHost`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/agent-bridge tests/test_claude_bridge.py
git commit -m "bridge: make the event listener bind address configurable (default loopback)"
```

---

### Task 5: Reply extraction moves into the relay (inline `turn_ended`)

**Files:**
- Create: `bin/agent-reply-extract` (small Python CLI wrapping the existing extractors)
- Modify: `bin/agent-bridge` — factor `extract_last_reply`/`extract_new_reply` so the CLI imports them; make the `turn_ended` handler prefer an inline `reply` field.
- Modify: `bin/claude-worker-done-relay` — compute the reply via the CLI (tracking its own byte-offset file) and include it inline in the POST.
- Test: `tests/test_claude_bridge.py` (extractor parity) + `tests/test_reply_extract.py` (CLI).

**Interfaces:**
- Produces: `bin/agent-reply-extract <transcript_path> <offset_file>` → prints the new reply text to stdout and advances the offset stored in `<offset_file>`. Reuses `extract_new_reply(transcript_path, offset)`.
- `turn_ended` payload gains `"reply"` (string). Bridge posts `reply` directly; if absent/empty it falls back to today's local transcript read (keeps a safety net for local workers mid-migration).

- [ ] **Step 1: Write the failing test for the CLI** (`tests/test_reply_extract.py`)

```python
import os, subprocess, tempfile, unittest
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(HERE, "bin", "agent-reply-extract")

class ReplyExtractCliTests(unittest.TestCase):
    def test_extracts_and_advances_offset(self):
        # minimal Claude transcript with one assistant text message
        tx = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        tx.write('{"type":"assistant","message":{"content":[{"type":"text","text":"hello"}]}}\n')
        tx.close()
        off = tx.name + ".off"
        out = subprocess.run(["python3", CLI, tx.name, off], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertIn("hello", out.stdout)
        self.assertTrue(os.path.exists(off))         # offset persisted
        # second run with the advanced offset yields nothing new
        out2 = subprocess.run(["python3", CLI, tx.name, off], capture_output=True, text=True)
        self.assertEqual(out2.stdout.strip(), "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_reply_extract -v`
Expected: FAIL — CLI does not exist

- [ ] **Step 3: Create the CLI reusing the bridge's extractor**

```python
#!/usr/bin/env python3
"""Print the new assistant reply in a Claude transcript since a stored byte
offset, then advance the offset. Used by claude-worker-done-relay so reply
extraction runs where the transcript lives (works for remote workers)."""
import importlib.util, os, sys
from importlib.machinery import SourceFileLoader

def _bridge():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent-bridge")
    loader = SourceFileLoader("claude_bridge", path)
    spec = importlib.util.spec_from_loader("claude_bridge", loader)
    mod = importlib.util.module_from_spec(spec); loader.exec_module(mod)
    return mod

def main():
    transcript, off_file = sys.argv[1], sys.argv[2]
    start = 0
    if os.path.exists(off_file):
        try: start = int(open(off_file).read().strip() or 0)
        except ValueError: start = 0
    cb = _bridge()
    text, new_offset = cb.extract_new_reply(transcript, start)   # (reply, offset)
    with open(off_file, "w") as f: f.write(str(new_offset))
    if text: sys.stdout.write(text)

if __name__ == "__main__":
    main()
```

(Confirm `extract_new_reply` returns `(text, new_offset)`; if it returns only text, add an offset return or compute `os.path.getsize(transcript)` as the new offset. Adjust the CLI to match the real signature.)

Make it executable: `chmod +x bin/agent-reply-extract` and add the symlink in `scripts/link.sh` alongside the other `bin/` tools.

- [ ] **Step 4: Run the CLI test to verify it passes**

Run: `python3 -m unittest tests.test_reply_extract -v`
Expected: PASS

- [ ] **Step 5: Make the done-relay send the reply inline**

In `bin/claude-worker-done-relay`, after it has `worker`, `transcript_path`, and the meta `chat`, compute the reply and add it to the JSON body:

```bash
OFF="$STATE_ROOT/$worker/reply-relay.off"
reply="$("$(dirname "$0")/agent-reply-extract" "$transcript_path" "$OFF" 2>/dev/null)"
# include "reply" in the JSON payload (use the same python -c json.dumps the
# script already uses to build the body), e.g. add key "reply": reply
```

Build the JSON with the existing `python3 -c` body construction so `reply` is JSON-escaped correctly.

- [ ] **Step 6: Make the bridge prefer the inline reply**

In the `turn_ended` handler (bin/agent-bridge ~3449), before the local transcript read:

```python
reply = (event.get("reply") or "").strip()
if not reply:
    reply = extract_last_reply(event["transcript_path"])  # existing local fallback
```

- [ ] **Step 7: Run the whole suite and commit**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS

```bash
git add bin/agent-reply-extract bin/agent-bridge bin/claude-worker-done-relay scripts/link.sh tests/test_reply_extract.py
git commit -m "relay: extract the reply where the transcript lives, send it inline"
```

---

### Task 6: Return-path configuration on dev-box and satellites (integration)

**Files:**
- Modify: `~/.config/claude-bridge/config.json` on dev-box (`listen_host`, `machines`, one repo's `host`).
- Modify: `~/.config/claude-workers/bridge-webhook` on each satellite (point at the parent).
- No app code; this wires and verifies the tailnet return path.

- [ ] **Step 1: Point the listener at the tailnet.** On dev-box set `"listen_host": "100.105.249.62"` and add:
  ```json
  "machines": { "mac": {"ssh": "christiannucifora@hc-002"}, "archbtw": {"ssh": "archbtw"} }
  ```
  Restart: `systemctl --user restart claude-bridge`. Verify: `ss -tln | grep 8765` shows it bound on the tailnet IP.

- [ ] **Step 2: Point each satellite's relays at the parent.** On mac and archbtw, set in `~/.config/claude-workers/bridge-webhook`:
  ```
  BRIDGE_WEBHOOK_URL=http://100.105.249.62:8765/event
  BRIDGE_WEBHOOK_SECRET=<same secret as dev-box>
  ```
  (Copy the exact secret from dev-box's file.)

- [ ] **Step 3: Add one remote repo and smoke-test the round trip.**
  ```bash
  bridge-ctl addrepo mactest /path/on/mac --host mac   # (Task 9 provides --host; until then hand-edit config)
  bridge-ctl start mactest
  # message #mactest in Discord; the worker replies via discord-notify + done-relay
  ```
  Expected: the reply lands back in `#mactest`; `journalctl --user -u claude-bridge` shows the `turn_ended`/`send` events arriving from the mac.

- [ ] **Step 4: Commit the dev-box config** (secrets stay out of git — config lives under `~/.config`, not the repo; nothing to commit here unless a redacted `config.example.json` gains the new keys — if so, update it):

```bash
git add config.example.json 2>/dev/null && git commit -m "docs: config example for machines/listen_host" || true
```

---

### Task 7: Inbound attachments to a remote worker's inbox

**Files:**
- Modify: `bin/agent-bridge` — the deliver/attachment-save path; add `copy_inbound_attachment(host_target, local_path, worker)`.
- Test: `tests/test_claude_bridge.py`

**Interfaces:**
- Produces: `remote_inbox_scp_argv(target, local_path, worker, state_root) -> list[str]` (pure, testable) — the `scp` argv to copy a saved attachment into `target:<state_root>/<worker>/inbox/`. The deliver path calls it only when `host_target is not None`; local workers keep the in-place save.

- [ ] **Step 1: Write the failing test**

```python
class InboundAttachmentTests(unittest.TestCase):
    def test_scp_argv_targets_remote_inbox(self):
        argv = cb.remote_inbox_scp_argv("me@h", "/tmp/a.png", "app",
                                        "/home/u/.local/state/claude-workers")
        self.assertEqual(argv[0], "scp")
        self.assertIn("/tmp/a.png", argv)
        self.assertIn("me@h:/home/u/.local/state/claude-workers/app/inbox/", argv)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_claude_bridge -v -k InboundAttachment`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
def remote_inbox_scp_argv(target, local_path, worker, state_root):
    dest = f"{target}:{state_root}/{worker}/inbox/"
    return ["scp", local_path, dest]
```

Wire it into the deliver path: after saving the attachment locally, if `host_target is not None`, `run_worker_cmd(None, remote_inbox_scp_argv(...))` (scp is local-invoked; it connects out) and ensure the remote inbox dir exists first via `ssh <target> mkdir -p <dir>`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_claude_bridge -v -k InboundAttachment`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/agent-bridge tests/test_claude_bridge.py
git commit -m "bridge: copy inbound attachments to a remote worker's inbox"
```

---

### Task 8: Outbound attachments from a satellite (multipart to `/event`)

**Files:**
- Modify: `bin/discord-notify` — when the bridge path is used and `-i` files are present, upload the file bytes as multipart to `/event` (instead of sending a path the bridge reads).
- Modify: `bin/agent-bridge` — the `/event` handler accepts `multipart/form-data`, verifies HMAC over the signed fields, and forwards the uploaded file to Discord through the bot.
- Test: `tests/test_claude_bridge.py` (HMAC over the multipart metadata; forwarding uses existing post-to-Discord path).

**Interfaces:**
- Produces: a `claude.worker.send` event may carry an uploaded file part; the bridge posts it to the mapped channel via the bot (reuse the existing attachment-post code, but from an uploaded temp file rather than a local path).

- [ ] **Step 1: Write the failing test** (signature still verifies when the body is the multipart's signed JSON field)

```python
class MultipartEventAuthTests(unittest.TestCase):
    def test_signed_metadata_verifies(self):
        secret = "s"; body = b'{"event_type":"claude.worker.send","chat":"discord:1","content":"x"}'
        sig = __import__("hmac").new(secret.encode(), body, __import__("hashlib").sha256).hexdigest()
        self.assertTrue(cb.verify_signature(body, sig, secret))
```

- [ ] **Step 2: Run test to verify it fails/passes**

Run: `python3 -m unittest tests.test_claude_bridge -v -k MultipartEventAuth`
Expected: PASS if `verify_signature` already exists (this test pins the contract the multipart handler must keep). If you refactor the handler, keep this green.

- [ ] **Step 3: Implement the multipart branch**

In `discord-notify` bridge path with attachments: build the same signed JSON metadata, then `curl` a multipart request to `/event` with the JSON as a signed field and each `-i` file as a file part (the script already uses `curl`; add `-F`). In `agent-bridge` `/event`: detect `multipart/form-data`, read the JSON field + verify its signature exactly as today, save each uploaded part to a temp file, and pass those temp paths into the existing Discord-post code.

- [ ] **Step 4: Run the suite and manually verify**

Run: `python3 -m unittest discover -s tests -v` → PASS
Manual: on a remote worker, `discord-notify -i /some/local.png "caption"` → image appears in the channel.

- [ ] **Step 5: Commit**

```bash
git add bin/discord-notify bin/agent-bridge tests/test_claude_bridge.py
git commit -m "attachments: upload remote worker files to the bridge for Discord egress"
```

---

### Task 9: `bridge-ctl addrepo --host` + `/addrepo` host arg + repos host column

**Files:**
- Modify: `bin/bridge-ctl` (the `addrepo` subcommand + `repos` listing), `bin/agent-bridge` (the `addrepo` event handler and the `/addrepo` slash command signature).
- Test: `tests/test_claude_bridge.py` for any pure mapping-building helper touched.

- [ ] **Step 1: Write/extend a test** for the addrepo config-entry builder (if a pure helper builds the repo dict, assert it stores `host`):

```python
class AddrepoHostTests(unittest.TestCase):
    def test_repo_entry_records_host(self):
        entry = cb.build_repo_entry(name="app", directory="/p", channel_id=1, host="mac")
        self.assertEqual(entry["host"], "mac")
    def test_default_host_absent_or_local(self):
        entry = cb.build_repo_entry(name="app", directory="/p", channel_id=1)
        self.assertIn(entry.get("host", "local"), (None, "local"))
```

(If no such helper exists, extract the repo-dict construction in the addrepo handler into `build_repo_entry(...)` first, then test it.)

- [ ] **Step 2: Run to verify it fails** → add helper → **Step 3: implement.** Thread an optional `--host <machine>` through `bridge-ctl addrepo` and the `claude.bridge.addrepo` event; add the optional `host` parameter to the `/addrepo` slash command; validate the host exists in `machines` (reuse `resolve_host_target` which raises on unknown). Add a `host` column to `bridge-ctl repos` output.

- [ ] **Step 4: Run suite** → PASS. Manual: `bridge-ctl addrepo demo /p --host archbtw` then `bridge-ctl repos` shows `archbtw`.

- [ ] **Step 5: Commit**

```bash
git add bin/bridge-ctl bin/agent-bridge tests/test_claude_bridge.py
git commit -m "ops: target a machine when creating a repo (addrepo --host)"
```

---

### Task 10: `agent-checkup` satellite probes

**Files:**
- Modify: `bin/agent-checkup` — for each machine in the bridge config, probe: SSH reachable, `agent-worker` on PATH, and its `bridge-webhook` URL points at the parent.

- [ ] **Step 1: Implement the probe loop.** Read `machines` from `~/.config/claude-bridge/config.json` (jq or python one-liner). For each:
  ```bash
  ssh -o ConnectTimeout=8 "$target" 'command -v agent-worker >/dev/null && \
    grep -q "BRIDGE_WEBHOOK_URL=http://100.105.249.62:8765" ~/.config/claude-workers/bridge-webhook' \
    && pass "satellite $name reachable + wired" || warn "satellite $name not ready"
  ```
- [ ] **Step 2: Run `agent-checkup`** on dev-box; expect a PASS line per configured satellite.
- [ ] **Step 3: Commit**

```bash
git add bin/agent-checkup
git commit -m "checkup: probe configured satellite machines"
```

---

## Self-Review

**Spec coverage:** Placement (Task 1, 9) · host-aware dispatch + remote peek (Tasks 2–3; peek is `worker_screen(..., ansi=True, host_target=...)` + local `term-shot`, already deployed) · listen_host (Task 4) · satellite webhook + relay inline extraction (Tasks 5–6) · inbound attachments (Task 7) · outbound attachments (Task 8) · addrepo/repos UX (Task 9) · agent-checkup (Task 10) · security (Task 6 config: tailnet bind + same HMAC secret) · backward-compat (asserted in Tasks 3 and 4). All spec sections map to a task.

**Placeholder scan:** Each code step carries real code. Two tasks say "confirm the real signature" (`extract_new_reply` return shape in Task 5) and "if no pure helper exists, extract one" (Tasks 4, 9) — these are explicit, bounded instructions, not deferred work.

**Type consistency:** `resolve_host_target(cfg, repo) -> target|None` feeds `host_target` used uniformly by `run_worker_cmd` and every `worker_*` helper and `remote_inbox_scp_argv`. `ssh_wrap(target, argv, env)` and `run_worker_cmd(host_target, argv, …)` names/params are consistent across Tasks 2, 3, 7. The `turn_ended` `reply` field (Task 5) is produced by `agent-reply-extract` and consumed by the bridge handler.

## Notes / risks carried from the spec

- SSH latency ~100–300 ms/command; if a hot path emerges, add an SSH `ControlMaster` persistent connection (not built now).
- Prefer binding the tailscale IP over `0.0.0.0`; confirm `100.105.249.62` is stable across reboots (it is the dev-box tailnet IP).
- `extract_new_reply` real return shape must be confirmed before Task 5 Step 3 (the CLI adapts to it).
