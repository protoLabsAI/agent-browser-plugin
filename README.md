# Agent Browser — browser automation plugin

A **protoAgent plugin** that gives the agent a real browser, backed by
**[agent-browser](https://github.com/vercel-labs/agent-browser)** (vercel-labs) — a
fast native-Rust CLI/daemon that drives Chrome over CDP with **accessibility-tree
snapshots** and compact `@eN` element refs.

Install into any protoAgent agent from this git URL — it's not tied to any one agent.

The model's loop is **open → snapshot → act on a `@ref` → verify**:
```
browser_open("example.com")
browser_snapshot()           # accessibility tree with @e1, @e2… refs
browser_click("@e2")         # act on a ref (or a CSS selector)
browser_fill("@e3", "…")
browser_get_text("body")     # read / extract
browser_screenshot()
browser_close()
```

## See it running — a working browser-driven agent

Want a complete, working example of an agent built around this plugin?
**[roxy](https://github.com/protoLabsAI/roxy)** is a protoLabs operator/orchestrator
agent that installs this plugin and is the agent in charge of building it out via its
own board. It consumes this repo the way you would (`plugin install` + a pinned
`plugins.lock`), enables it, and ships the surrounding agent. Fork it as a starting
point.

## What it contributes

- **Browser tools** — subprocess wrappers over the CLI: `browser_open`,
  `browser_snapshot`, `browser_click`, `browser_fill`, `browser_type`,
  `browser_get_text`/`get_html`/`get_value`, `browser_press`, `browser_hover`,
  `browser_eval`, `browser_screenshot`, `browser_back`/`forward`/`reload`,
  `browser_close`, `browser_dashboard`.
- **Skill** — a discovery skill that defers to the CLI's always-current workflow
  content (`agent-browser skills get core`), so instructions never go stale.
- **Workflows** — declarative browser recipes (browse-and-extract, fill-a-form, …).
- **Browser panel** — a console view (ADR 0026) for watching/driving the browser. Two
  modes, set by `panel_mode`:
  - **`full` (default)** — **embeds agent-browser's dashboard inline** (viewport + activity/
    console/network feeds) at its own **local origin** (`http://<host>:<port>/`). Best for a
    **local setup** (console + agent-browser on one machine). Because the dashboard is a
    Next.js app with root-absolute assets (no base-path), it only loads at its own origin —
    so when the console is opened **remotely** (a fleet member, a non-loopback host, or over
    https) its `localhost` isn't reachable from your browser, and the panel shows a **clear
    error** (pointing you at `minimal`) rather than a blank frame.
  - **`minimal`** — a **live screenshot** of the viewport + a nav toolbar, all through the
    **gated same-origin routes**. Works everywhere (host and member), no dashboard daemon
    needed — use it for a remote/member agent.

  Either mode can **start the dashboard from the panel** (no terminal) — the Start/Stop
  control hits the gated `POST /api/plugins/agent_browser/dashboard`.

## Requirements

- **protoAgent ≥ 0.27.0** (console views, plugin tools).
- The **`agent-browser`** binary on PATH:
  ```bash
  npm i -g agent-browser && agent-browser install   # downloads Chrome for Testing
  ```
  (Homebrew and Cargo installs also work — see the upstream README.)

## Install

```bash
python -m server plugin install https://github.com/protoLabsAI/agent-browser-plugin --ref main
python -m server plugin enable agent_browser        # then restart
```

```yaml
plugins:
  enabled: [agent_browser]

agent_browser:
  binary: agent-browser
  dashboard_port: 4848
```

## Layout

| File | What |
|---|---|
| `tools.py` | the browser tools — subprocess wrappers over the `agent-browser` CLI |
| `browser_panel.py` | the Browser panel — `minimal` (live viewport + nav + dashboard control) and `full` (a launcher for the dashboard) |
| `lifecycle.py` | the dashboard daemon surface — start on boot, stop on shutdown (ADR 0018) |
| `skills/` | the discovery skill (defers to `agent-browser skills get core`) |
| `workflows/` | declarative browser recipes |
| `tests/` | the host-free pytest suite (subprocess mocked — no binary needed) |
| `__init__.py` | `register()` — wires tools + panel + lifecycle; skills/workflows auto-discovered |

The operator knobs (panel mode, headed, allowed domains, profile, device, …) are editable in
**Settings ▸ Plugins ▸ Agent Browser**, or under `agent_browser:` in `langgraph-config.yaml`.

## Development

```bash
pip install -r requirements-dev.txt
pytest -q          # host-free — subprocess is mocked, no agent-browser binary needed
ruff check .
```

CI runs the same on every PR.

Ships **disabled**; nothing runs until you enable it and the `agent-browser` binary
is installed.
