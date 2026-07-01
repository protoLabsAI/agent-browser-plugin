"""Interactive browser streaming — a CDP screencast + input bridge.

The `interactive` panel mode renders a live, *drivable* viewport instead of a
screenshot poll. It works by attaching a **second CDP client** to the same Chrome
that agent-browser drives (the CLI hands us the endpoint via `agent-browser get
cdp-url`), running **`Page.startScreencast`** for event-driven JPEG frames, and
forwarding operator input back with **`Input.dispatch*`**. A second CDP client
coexists with agent-browser's own session — the agent and the operator can both
touch the page.

Everything is bridged to the panel over a **gated same-origin WebSocket**
(`/api/plugins/agent_browser/stream`), so it inherits the operator bearer gate and
rides the fleet proxy — the interactive viewport works on a remote member, not just
the host (the thing the `full` dashboard embed never could).

This module is split so the CDP-facing brains are pure and host-free-testable:
``_http_base_from_ws`` / ``pick_page_target`` / ``input_to_cdp`` have no IO. The
``CDPStream`` async client and ``resolve_page_target`` do the talking; the WebSocket
route lives in ``browser_panel``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import subprocess
import time
import urllib.request

log = logging.getLogger("protoagent.plugins.agent_browser")


# ── WebSocket auth: single-use tickets ─────────────────────────────────────────
# The host's operator-bearer gate is HTTP-only middleware — it does NOT cover
# WebSocket handshakes (a browser `new WebSocket()` can't send an Authorization
# header anyway). So the stream WS gates itself: the panel first calls the *gated*
# `POST /stream-ticket` (bearer-checked by the host, so only an authenticated
# console gets one), then presents the ticket on the WS URL. This mirrors the host's
# own SSE-token escape hatch for `/api/events`. Short-lived + single-use so a ticket
# leaked into a proxy/access log is near-worthless.
_TICKET_TTL = 30.0
_tickets: dict[str, float] = {}


def _prune_tickets(now: float) -> None:
    for k in [k for k, exp in _tickets.items() if exp < now]:
        _tickets.pop(k, None)


def mint_ticket() -> str:
    """Issue a single-use ticket good for ~30s. Called only from the gated HTTP
    route, so possession of a ticket proves the caller cleared the operator gate."""
    now = time.monotonic()
    _prune_tickets(now)
    t = secrets.token_urlsafe(24)
    _tickets[t] = now + _TICKET_TTL
    return t


def consume_ticket(ticket: str) -> bool:
    """Validate + burn a ticket. False if unknown/expired (→ reject the WS)."""
    now = time.monotonic()
    _prune_tickets(now)
    exp = _tickets.pop(ticket, None) if ticket else None
    return exp is not None and exp >= now


# ── pure helpers (host-free-testable — no IO) ──────────────────────────────────

def _http_base_from_ws(ws_url: str) -> str:
    """`ws://127.0.0.1:52886/devtools/browser/…` → `http://127.0.0.1:52886`. The
    CDP HTTP endpoints (`/json/list`, `/json/version`) live at the same host:port."""
    rest = ws_url.split("://", 1)[-1]
    authority = rest.split("/", 1)[0]
    return "http://" + authority


def pick_page_target(targets: list[dict], current_url: str = "") -> str | None:
    """Choose which CDP target to stream from a `/json/list` array. Prefer the
    `page` matching the session's current URL (the active tab); else the first real
    `page` (skipping chrome:// / devtools surfaces). Returns its
    `webSocketDebuggerUrl`, or None if there's no page to stream."""
    pages = [t for t in targets
             if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    if not pages:
        return None
    cur = (current_url or "").strip()
    if cur:
        for t in pages:
            if t.get("url", "") == cur:
                return t["webSocketDebuggerUrl"]
    real = [t for t in pages if not t.get("url", "").startswith(("chrome://", "devtools://"))]
    return (real or pages)[0]["webSocketDebuggerUrl"]


# Modifier bit-mask CDP expects on Input events (Alt=1, Ctrl=2, Meta=4, Shift=8).
def _modifiers(m: dict) -> int:
    return ((1 if m.get("alt") else 0) | (2 if m.get("ctrl") else 0)
            | (4 if m.get("meta") else 0) | (8 if m.get("shift") else 0))


_MOUSE_TYPE = {"down": "mousePressed", "up": "mouseReleased", "move": "mouseMoved"}


def input_to_cdp(msg: dict) -> tuple[str, dict] | None:
    """Translate a panel input message → a `(cdp_method, params)` pair, or None if
    it isn't a drivable input. Coordinates arrive already in CSS pixels (the client
    maps canvas→page against the frame metadata), so we pass them straight through.

    Panel messages:
      {t:"mouse", action:"down|up|move", x, y, button?, clickCount?, buttons?, mods…}
      {t:"wheel", x, y, dx, dy, mods…}
      {t:"key",   action:"down|up", key, code?, text?, keyCode?, mods…}
    """
    t = msg.get("t")
    if t == "mouse":
        typ = _MOUSE_TYPE.get(msg.get("action", ""))
        if not typ:
            return None
        p = {"type": typ, "x": float(msg.get("x", 0)), "y": float(msg.get("y", 0)),
             "modifiers": _modifiers(msg)}
        if typ != "mouseMoved":
            p["button"] = msg.get("button", "left")
            p["clickCount"] = int(msg.get("clickCount", 1))
        p["buttons"] = int(msg.get("buttons", 0))
        return "Input.dispatchMouseEvent", p
    if t == "wheel":
        return "Input.dispatchMouseEvent", {
            "type": "mouseWheel", "x": float(msg.get("x", 0)), "y": float(msg.get("y", 0)),
            "deltaX": float(msg.get("dx", 0)), "deltaY": float(msg.get("dy", 0)),
            "modifiers": _modifiers(msg)}
    if t == "key":
        typ = {"down": "keyDown", "up": "keyUp"}.get(msg.get("action", ""))
        if not typ:
            return None
        text = msg.get("text", "")
        # A keyDown that produces a character must be dispatched as "keyDown" with
        # text; CDP turns text-bearing keyDowns into the actual input.
        p = {"type": typ, "key": msg.get("key", ""), "code": msg.get("code", ""),
             "modifiers": _modifiers(msg)}
        if msg.get("keyCode"):
            p["windowsVirtualKeyCode"] = int(msg["keyCode"])
            p["nativeVirtualKeyCode"] = int(msg["keyCode"])
        if typ == "keyDown" and text:
            p["text"] = text
        return "Input.dispatchKeyEvent", p
    return None


# ── the CDP client (IO) ────────────────────────────────────────────────────────

def resolve_page_target(binary: str, timeout: float = 10.0) -> tuple[str | None, str]:
    """Ask agent-browser for its Chrome CDP endpoint, then find the active page's
    per-target WebSocket. Returns ``(page_ws_url | None, note)`` — note carries a
    human-readable reason when there's nothing to stream (no session / no page)."""
    try:
        cdp = subprocess.run([binary, "get", "cdp-url"], capture_output=True,
                             text=True, timeout=timeout)
    except FileNotFoundError:
        return None, f"{binary!r} not on PATH"
    except subprocess.TimeoutExpired:
        return None, "agent-browser get cdp-url timed out"
    browser_ws = (cdp.stdout or "").strip().splitlines()[0].strip() if cdp.stdout else ""
    if cdp.returncode != 0 or not browser_ws.startswith("ws"):
        return None, ((cdp.stderr or "").strip() or "no CDP url — is a session open?")
    base = _http_base_from_ws(browser_ws)
    cur = ""
    try:
        u = subprocess.run([binary, "get", "url"], capture_output=True, text=True, timeout=timeout)
        cur = (u.stdout or "").strip().splitlines()[0].strip() if u.returncode == 0 else ""
    except Exception:  # noqa: BLE001 — current url is a nicety for tab selection
        cur = ""
    try:
        with urllib.request.urlopen(base + "/json/list", timeout=timeout) as r:
            targets = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        return None, f"CDP /json/list unreachable at {base}: {e}"
    page = pick_page_target(targets, cur)
    return (page, "" if page else "no page target to stream (open a URL first)")


class CDPStream:
    """A minimal async CDP client over one page target: start a screencast, ack
    frames, and dispatch input. `frame_cb(jpeg_bytes, metadata)` is called for each
    `Page.screencastFrame`. Requires the ``websockets`` package (a uvicorn extra —
    already present wherever the host serves WebSockets)."""

    def __init__(self, page_ws_url: str, frame_cb):
        self._url = page_ws_url
        self._frame_cb = frame_cb
        self._ws = None
        self._id = 0
        self._reader: asyncio.Task | None = None

    async def __aenter__(self):
        import websockets
        self._ws = await websockets.connect(self._url, max_size=None, open_timeout=10)
        self._reader = asyncio.create_task(self._read_loop())
        return self

    async def __aexit__(self, *exc):
        if self._reader:
            self._reader.cancel()
        if self._ws:
            await self._ws.close()

    async def _send(self, method: str, params: dict | None = None) -> int:
        self._id += 1
        await self._ws.send(json.dumps({"id": self._id, "method": method, "params": params or {}}))
        return self._id

    async def start_screencast(self, max_w: int = 1280, max_h: int = 800, quality: int = 60):
        await self._send("Page.enable")
        await self._send("Page.startScreencast", {"format": "jpeg", "quality": quality,
                         "maxWidth": max_w, "maxHeight": max_h, "everyNthFrame": 1})

    async def dispatch(self, msg: dict):
        cmd = input_to_cdp(msg)
        if cmd:
            await self._send(*cmd)

    async def _read_loop(self):
        import base64
        async for raw in self._ws:
            try:
                m = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue
            if m.get("method") == "Page.screencastFrame":
                p = m["params"]
                try:
                    await self._frame_cb(base64.b64decode(p["data"]), p.get("metadata", {}))
                finally:
                    await self._send("Page.screencastFrameAck", {"sessionId": p["sessionId"]})
