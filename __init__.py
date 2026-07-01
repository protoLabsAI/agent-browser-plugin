"""agent_browser — browser automation for protoAgent, backed by vercel-labs/agent-browser.

Composition over construction: this plugin is a thin shell over the `agent-browser`
CLI/daemon. It contributes the browser **tools** (subprocess wrappers), a discovery
**skill** + browser **workflows** (auto-discovered from skills/ and workflows/), and an
interactive **Browser panel** console view — a live, drivable CDP-screencast viewport
(browser_stream bridges Chrome's CDP to a canvas over a gated WebSocket). It does NOT
reimplement browser automation or a renderer.

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

    # Interactive Browser panel console view. Register it best-effort so the tools still
    # serve if the panel can't import. TWO routers at DISTINCT prefixes: the PAGE stays on
    # the public /plugins/agent_browser (an iframe page-load can't carry a bearer); the
    # DATA routes (the nav toolbar, the stream ticket + the /stream WS) mount under
    # /api/plugins/agent_browser so the HTTP ones inherit the operator bearer gate
    # (plugin-view rule 2). The WS gates itself with a single-use ticket — the host's auth
    # middleware doesn't cover WS handshakes.
    try:
        from .browser_panel import build_panel_data_router, build_panel_router
        registry.register_router(build_panel_router(cfg))
        registry.register_router(build_panel_data_router(cfg), prefix="/api/plugins/agent_browser")
    except ImportError:
        log.info("[agent_browser] browser panel not present yet — tools still serve")
    except Exception:  # noqa: BLE001
        log.exception("[agent_browser] mounting browser panel failed")

    # skills/ and workflows/ are auto-discovered (ADR 0027) — no register call.
    log.info("[agent_browser] registered browser tools (binary=%s)", cfg.get("binary", "agent-browser"))
