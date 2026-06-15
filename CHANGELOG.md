# Changelog

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
