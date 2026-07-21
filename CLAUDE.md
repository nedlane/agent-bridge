# agent-bridge — project notes for agents

This repo is the Discord ↔ agent-worker bridge. The CLI tools live in `bin/`
(`agent-bridge` daemon, `agent-worker` per-worker driver, `bridge-ctl`,
`discord-notify`, the done/todo relays). Each Discord channel maps to a repo
worker; workers run in tmux sessions named `cw-<name>`.

## Deploy topology (read before editing anything in `bin/`)

There are **two checkouts** of this repo on the desktop:

- `~/projects/agent-bridge` — standalone clone; **this is the agent-bridge
  worker's cwd**, so this is the CLAUDE.md that worker reads.
- `~/dotfiles/hosts/wsl-desktop/agent-bridge` — a **git submodule** of the
  dotfiles repo. **This is what is LIVE**: `~/.local/bin/{agent-bridge,
  agent-worker,bridge-ctl,discord-notify,...}` symlink into it, and the systemd
  `claude-bridge` service runs it.

Editing files in the projects clone does **not** change the running system.
The `agent-bridge` daemon loads its config **once at startup** (no SIGHUP /
hot-reload), so config or code changes need a service restart:
`systemctl --user restart claude-bridge` (brief; the `cw-*` tmux workers
survive it). `agent-worker` is exec'd fresh per action, so edits to it take
effect on the next send without a restart. Full deploy path: get code into the
submodule → `scripts/link.sh` from the live checkout → restart the bridge.

## The Web UI (agent worker web console)

The browser console for driving these workers is a **separate project**, not
part of this repo:

**Repo:** `~/projects/claude-worker-webui`

It is a two-machine setup on the tailnet:

```
browser ──(cloudflared tunnel)──► minipc :8080 ──(tailnet HTTP+WS)──► desktop :8790
                                   web console                        worker-api
```

- **desktop** (`100.87.197.50`) runs **`worker-api`** — an aiohttp daemon
  (`desktop/worker-api.py`, port **8790**) that wraps the worker CLI
  (`claude-worker`, a compat symlink to `agent-worker`) and exposes an HTTP +
  WebSocket API. Bearer-token auth; token at `~/.config/worker-api/token`.
- **minipc** (`100.97.177.18`) runs **`worker-webui`** — a Node/Express app
  (`minipc/server.js`, port **8080**) that authenticates the browser, serves
  the SPA from `minipc/public/`, and reverse-proxies `/api/*` (and the terminal
  WebSocket) to the desktop, injecting the bearer token the browser never sees.

### How to access it

- **Public:** via the cloudflared tunnel on the minipc (exposes
  `localhost:8080`). The public hostname is defined on the **minipc** in
  `~/.cloudflared/config.yml` (or the Cloudflare Zero Trust → Networks →
  Tunnels dashboard). Check there for the current URL.
- **Tailnet-direct (no tunnel):**
  - Web console: `http://100.97.177.18:8080` — login at `/login`.
  - Desktop API health: `http://100.87.197.50:8790/healthz`.
- **Auth:** `AUTH_MODE=password` by default — the app password is
  `APP_PASSWORD` in the minipc's `~/.config/worker-api/webui.env`. Optional
  Authentik OIDC (`AUTH_MODE=oidc`, config-only) is documented in the webui
  repo's README.

### How to edit / deploy it

Repo layout (`~/projects/claude-worker-webui`):

```
desktop/  worker-api.py, worker-api.service, README.md (API reference)
minipc/   server.js, package.json, worker-webui.service, webui.env.example
          public/ (SPA: index.html, app.js, styles.css, login.html,
                   vendor/xterm.js + xterm.css + xterm-addon-fit.js)
deploy/   deploy.sh  (idempotent; run FROM THE DESKTOP)
```

Deploy from the **desktop**:

```bash
cd ~/projects/claude-worker-webui
./deploy/deploy.sh
```

The script installs/(re)starts the desktop `worker-api` unit, rsyncs `minipc/`
to the minipc over `tailscale ssh`, runs `npm install`, and installs/(re)starts
the minipc `worker-webui` unit. Both are `systemd --user` services with linger
enabled. It prints health probes for both hosts at the end.

- **Frontend changes** → edit `minipc/public/` (plain SPA: `index.html`,
  `app.js`, `styles.css`), then deploy.
- **Backend/API changes** (add endpoints, wrap more of the worker CLI) → edit
  `desktop/worker-api.py`, then deploy. Its current routes:
  `GET /api/workers`, `GET /api/workers/{name}`, `.../screen`, `.../log`;
  `POST .../send|start|stop|restart`; `GET /api/bridge/health`; and the
  `GET /api/workers/{name}/attach` WebSocket PTY. (No harness/model-switch
  endpoint yet.)
- **Proxy/auth changes** → edit `minipc/server.js`, then deploy.

Note the desktop worker-api runs as its own systemd `--user` unit; after
editing `worker-api.py` you can iterate with
`systemctl --user restart worker-api` on the desktop instead of a full deploy.
