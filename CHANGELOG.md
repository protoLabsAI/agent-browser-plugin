# Changelog

## v0.6.0
- **The Browser panel is now a fully interactive, drivable viewport — and it is the ONLY mode.**
  A live **CDP screencast** — event-driven JPEG frames (not a screenshot poll) painted on a
  `<canvas>` — forwards your **mouse, keyboard, and scroll** back into the page via
  `Input.dispatch*`. You can click, type, and scroll the real browser from the console, right
  alongside the agent. Because every byte is a **gated same-origin WebSocket**, it works on the
  host **and** a remote fleet member.
- **How it works.** A second CDP client attaches to the same Chrome agent-browser drives (via
  `agent-browser get cdp-url`); `browser_stream.py` bridges `Page.startScreencast` ⇆ the panel.
  The nav toolbar (url / back / forward / reload) reuses the gated HTTP `/nav` route.
- **WebSocket auth.** The host's operator-bearer gate is HTTP-only and does **not** cover WS
  handshakes, so the stream self-gates: the panel mints a **single-use ticket** from the gated
  `POST /stream-ticket` (bearer-checked) and presents it on the WS URL; the handler validates and
  burns it. Safe in gated deployments, transparent in open ones.
- **Full switchover — the old dashboard-embed approach is removed** (no backward compatibility):
  - `panel_mode` is gone (there is one panel now); so is the screenshot `minimal` mode and its
    `/shot` route, and the `full` dashboard-embed page.
  - The **`browser_dashboard` tool is removed** (16 tools now) along with the boot/shutdown
    **dashboard lifecycle** (`lifecycle.py`) and the panel's `/dashboard` control routes.
  - Removed config: `panel_mode`, `dashboard_port`, `manage_dashboard`. The interactive panel
    talks CDP directly and never needs agent-browser's separate dashboard daemon.

## v0.5.1
- **Full mode: an "Open ↗" button** to pop the dashboard out into a full browser tab, alongside the
  inline embed. Shown whenever the dashboard is on this machine (loopback host) — including over an
  https console, where the new-tab nav reaches the http dashboard even though the embedded frame
  can't.

## v0.5.0
- **`full` is the default again, and it embeds the dashboard inline.** Full mode iframes
  agent-browser's dashboard at its own **local origin** (`http://<host>:<port>/`) — so on a local
  setup (console + agent-browser on one machine) you get the real dashboard, feeds and all, right
  in the panel. (v0.4.0 turned it into a new-tab launcher; this brings the inline embed back, the
  right way — at the dashboard's origin, not through the impossible sub-path proxy.)
- **A clear error instead of a blank frame when it can't embed.** If the console is opened remotely
  (a fleet member, a non-loopback host, or over https) the dashboard's `localhost` isn't reachable
  from your browser — the panel detects that (loopback host + not fleet-proxied + not https) and
  says so, pointing you at `panel_mode: minimal` (which still works everywhere). Start/Stop the
  dashboard from the panel as before; a not-yet-running dashboard shows a Start prompt.

## v0.4.0
- **The Browser panel works out of the box now.** The default `panel_mode` is **`minimal`** — a
  live screenshot + nav toolbar driven through the gated same-origin routes; it works everywhere
  (host + member), no dashboard daemon needed.
- **`full` mode is now a launcher, not an embed.** The dashboard is a Next.js app whose assets are
  **root-absolute** (`/_next/...`) with no base-path, so it can't render under a sub-path panel —
  that was the long-standing blank panel. Full mode now opens the dashboard at its **own origin**
  ("Open dashboard ↗", works on a local/host setup) and the **dead sub-path reverse proxy
  (HTTP + WebSocket) is removed**.
- **Start the dashboard from the UI** — a live status dot + **Start / Stop** control in the panel
  (gated `GET`/`POST /api/plugins/agent_browser/dashboard`), so you never need a terminal. Verified
  end-to-end against the real `agent-browser` binary (status → start → running → stop).

## v0.3.0
- **Host-free test suite** (`tests/`) — the tool subprocess wrappers (arg-building +
  graceful error degradation), the panel routers (page / four-rules / gating / dashboard
  proxy 502), `register()` wiring, the dashboard lifecycle, and manifest/version coherence.
  subprocess is mocked, so no `agent-browser` binary and no real browser are needed.
- **CI** (`.github/workflows/ci.yml`) — `ruff check` + `pytest` on every PR.
- **Settings** — the operator config (panel mode, headed, allowed domains, profile, …) is
  now editable in **Settings ▸ Plugins** (`settings:` block; `panel_mode` is a select).
- Fixed two dead locals in `build_panel_router` (lint was red — it had gone unnoticed
  without CI) and re-synced `pyproject.toml` (was `0.1.0`) with the manifest version.

## v0.2.0
- Gated the minimal-mode `shot`/`nav` data routes under `/api/plugins/agent_browser`
  (plugin-view rule 2) and adopted the DS plugin-kit for the panel chrome (#8).

## v0.1.1
- Serve the full-mode dashboard same-origin via a reverse proxy so it rides the fleet
  proxy on a member box instead of a hardcoded `localhost:PORT` (#6/#7); link the DS kit
  same-origin (`/_ds/`) instead of the CDN (#5).

## v0.1.0
- Initial release: browser tools over the `agent-browser` CLI, a discovery skill, browser
  workflows, the Browser panel (`full` | `minimal`), and the dashboard lifecycle surface.
