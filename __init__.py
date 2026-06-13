"""agent_browser — browser automation for protoAgent, backed by vercel-labs/agent-browser.

Composition over construction: this plugin is a thin shell over the `agent-browser`
CLI/daemon. It contributes the browser **tools** (subprocess wrappers), a discovery
**skill** + browser **workflows** (auto-discovered from skills/ and workflows/), and a
**Browser panel** console view that embeds agent-browser's own live dashboard — it
does NOT reimplement browser automation or a renderer.

Ships DISABLED. Enable with `plugins: { enabled: [agent_browser] }` and put the
`agent-browser` binary on PATH (`npm i -g agent-browser && agent-browser install`).
"""

from __future__ import annotations

import logging

log = logging.getLogger("protoagent.plugins.agent_browser")


def register(registry) -> None:
    cfg = registry.config or {}

    # Browser tools (subprocess wrappers over the agent-browser CLI).
    try:
        from .tools import get_browser_tools
        for t in get_browser_tools(cfg):
            registry.register_tool(t)
    except Exception:  # noqa: BLE001 — tools are the foundation; log loudly if they fail
        log.exception("[agent_browser] registering browser tools failed")

    # Browser panel console view (embeds agent-browser's dashboard). Built out by the
    # board; register it best-effort so the foundation works before the view lands.
    # TWO routers at DISTINCT prefixes: the PAGE (+ the iframe-loaded /panel/dash
    # proxy, which can't carry a bearer) stays on the public /plugins/agent_browser;
    # the shot/nav DATA routes mount under /api/plugins/agent_browser so they
    # inherit the operator bearer gate (plugin-view rule 2).
    try:
        from .browser_panel import build_panel_data_router, build_panel_router
        registry.register_router(build_panel_router(cfg))
        registry.register_router(build_panel_data_router(cfg), prefix="/api/plugins/agent_browser")
    except ImportError:
        log.info("[agent_browser] browser panel not present yet — tools still serve")
    except Exception:  # noqa: BLE001
        log.exception("[agent_browser] mounting browser panel failed")

    # Lifecycle (ADR 0018): on shutdown, stop the dashboard daemon we manage so it
    # doesn't outlive the server (dashboard-only; the session is left alone).
    try:
        from .lifecycle import make_dashboard_surface
        start, stop = make_dashboard_surface(cfg)
        registry.register_surface(start, stop=stop, name="agent-browser-dashboard")
    except Exception:  # noqa: BLE001 — lifecycle is best-effort; tools/panel still serve
        log.exception("[agent_browser] registering lifecycle surface failed")

    # skills/ and workflows/ are auto-discovered (ADR 0027) — no register call.
    log.info("[agent_browser] registered browser tools (binary=%s, dashboard:%s, manage=%s)",
             cfg.get("binary", "agent-browser"), cfg.get("dashboard_port", 4848),
             cfg.get("manage_dashboard", True))
