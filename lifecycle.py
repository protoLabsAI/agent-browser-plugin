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
    """Return ``(start, stop)`` for ``register_surface`` — full ownership of the
    managed dashboard: ``start`` launches it on boot, ``stop`` tears it down on
    shutdown. Both best-effort and gated by ``manage_dashboard`` (so a shared/
    persistent dashboard, ``manage_dashboard: false``, is left untouched)."""
    cfg = cfg or {}
    binary = str(cfg.get("binary") or "agent-browser")
    manage = bool(cfg.get("manage_dashboard", True))
    port = int(cfg.get("dashboard_port", 4848))

    async def start():
        if not manage:
            return None
        try:
            await asyncio.to_thread(
                lambda: subprocess.run([binary, "dashboard", "start", "--port", str(port)],
                                       capture_output=True, text=True, timeout=30))
            log.info("[agent_browser] dashboard daemon started on :%s", port)
        except Exception:  # noqa: BLE001 — best-effort; the binary may not be installed yet
            log.warning("[agent_browser] dashboard start on boot failed", exc_info=True)
        return None

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
