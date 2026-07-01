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
  `browser_close`.
- **Skill** — a discovery skill that defers to the CLI's always-current workflow
  content (`agent-browser skills get core`), so instructions never go stale.
- **Workflows** — declarative browser recipes (browse-and-extract, fill-a-form, …).
- **Browser panel** — a console view (ADR 0026) that is a **fully drivable viewport**. A live
  **CDP screencast** (event-driven JPEG frames, not a screenshot poll) is painted on a
  `<canvas>`, and your **mouse / keyboard / scroll** are forwarded back into the page via
  `Input.dispatch*` — so you can click, type, and scroll the real browser from the console,
  alongside the agent. Everything rides a **gated same-origin WebSocket**, so it works on the
  **host and a remote fleet member** alike. A second CDP client attaches to the same Chrome
  agent-browser drives (`agent-browser get cdp-url`); `browser_stream.py` does the bridging.

  The viewport is **full-stretch and responsive** — it resizes Chrome's layout viewport to your
  dock's size (× device-pixel-ratio) as you expand/collapse it, so the page reflows to fill rather
  than sitting as a fixed box (letterboxed with `object-fit` during the resize so it never
  distorts). The screencast **re-arms on every navigation** so you see the agent move through
  pages and sub-pages (not just the first load), and it **keeps updating even when the panel isn't
  focused** (the page is pinned focused/visible over CDP; headed windows launch with
  anti-backgrounding flags). Tune sharpness with `stream_quality`.

  When no page is open the panel shows a **Start button** (not a dead end). Set `home_url` to a
  page and the panel **auto-opens** it — a homepage — otherwise Start opens `about:blank`.

  **Getting past bot walls** (Google, Reddit, Cloudflare): set `stealth: true` (drops the
  automation flag + real UA when headless), and — most reliably — `headed: true` with a logged-in
  Chrome `profile`. `user_agent` / `browser_args` are there for fine control. No setting defeats
  detection entirely.

  **WebSocket auth:** the host's operator-bearer gate is HTTP-only and doesn't cover WS
  handshakes, so the stream self-gates — the panel mints a **single-use ticket** from the gated
  `POST /api/plugins/agent_browser/stream-ticket` and presents it on the WS URL.

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
```

## Layout

| File | What |
|---|---|
| `tools.py` | the browser tools — subprocess wrappers over the `agent-browser` CLI |
| `browser_panel.py` | the Browser panel page + routes — the interactive canvas + the gated nav / stream-ticket / WS-stream routes |
| `browser_stream.py` | the CDP bridge — screencast frames out, input in, viewport resize + nav re-arm; the WS ticket auth |
| `runtime.py` | shared launch-flag builder (headed / profile / device / stealth), used by the tools and the panel |
| `skills/` | the discovery skill (defers to `agent-browser skills get core`) |
| `workflows/` | declarative browser recipes |
| `tests/` | the host-free pytest suite (subprocess mocked — no binary needed) |
| `__init__.py` | `register()` — wires tools + the interactive panel; skills/workflows auto-discovered |

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
