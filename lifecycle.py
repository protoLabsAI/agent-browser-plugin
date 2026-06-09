"""Plugin lifecycle (ADR 0018) — tear down the dashboard daemon on shutdown.

agent-browser's dashboard runs as a standalone background daemon. Without a stop
hook it outlives the agent (orphaned on every restart). This registers a surface
whose ``stop`` best-effort runs ``agent-browser dashboard stop`` so the daemon dies
with the server. Dashboard-only by design — the browser session is left alone.

Gated by ``manage_dashboard`` (default true): set it false for a shared/persistent
dashboard you don't want the agent to stop.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess

log = logging.getLogger("protoagent.plugins.agent_browser")


def make_dashboard_surface(cfg: dict | None):
    """Return ``(start, stop)`` for ``register_surface``. ``start`` is a no-op (we
    don't auto-launch the dashboard); the point is the ``stop`` teardown."""
    cfg = cfg or {}
    binary = str(cfg.get("binary") or "agent-browser")
    manage = bool(cfg.get("manage_dashboard", True))

    async def start():
        return None  # nothing to start — the dashboard is launched on demand

    async def stop():
        if not manage:
            return
        try:
            await asyncio.to_thread(
                lambda: subprocess.run([binary, "dashboard", "stop"],
                                       capture_output=True, text=True, timeout=15))
            log.info("[agent_browser] dashboard daemon stopped on shutdown")
        except Exception:  # noqa: BLE001 — teardown is best-effort; never block shutdown
            log.warning("[agent_browser] dashboard stop on shutdown failed", exc_info=True)

    return start, stop
