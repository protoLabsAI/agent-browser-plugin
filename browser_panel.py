"""Browser panel console view (ADR 0026) — two modes, set by ``panel_mode``.

- ``full`` (default): iframes agent-browser's own live dashboard (viewport +
  activity/console/network/… feeds), reusing their renderer wholesale. The
  dashboard is served **through this plugin's router** (a same-origin reverse
  proxy under ``/plugins/agent_browser/panel/dash``) so the iframe rides the
  fleet proxy (ADR 0042) on the host AND on a member — never a hardcoded
  ``http://localhost:PORT`` (issue #6: that URL resolved against the operator's
  own machine, not the member box → "refused to connect").
- ``minimal``: a viewport-only page we render ourselves — a live screenshot (polled
  from the CLI, same-origin, no WS-protocol dependency) plus a slim nav toolbar
  (url / back / forward / reload). "Expose less": just the page, nothing else.

Self-contained vanilla JS (no build step). Every fetch / iframe-src / asset is
derived from ``base = location.pathname.split("/plugins/")[0]`` (="" on the host,
``/agents/<slug>`` when proxied) so the page is same-origin + slug-aware — the rule
that keeps the postMessage token/theme handshake working through the fleet proxy.
The page reads/drives the browser only through the CLI behind same-origin routes;
the agent's own browser_* tools are the primary driver — this is an operator viewport.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
import time

# Imported at MODULE scope (not inside the factory) so FastAPI can resolve the
# string annotations these routes carry — with `from __future__ import annotations`
# every annotation is a string and FastAPI's get_type_hints() looks them up in the
# module globals, so `request: Request` / `websocket: WebSocket` must live here.
# Safe: the host always provides fastapi, and __init__.py catches ImportError
# (tools still serve if the panel can't import).
from fastapi import APIRouter, Body, Request, WebSocket
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)

log = logging.getLogger("protoagent.plugins.agent_browser")

_SHOT_PATH = os.path.join(tempfile.gettempdir(), "agent_browser_panel.png")
_shot_ts = 0.0  # last successful capture (cheap throttle so polling can't storm the CLI)


def build_panel_router(cfg: dict | None):

    cfg = cfg or {}
    port = int(cfg.get("dashboard_port", 4848))
    mode = str(cfg.get("panel_mode", "full")).strip().lower()
    binary = str(cfg.get("binary") or "agent-browser")
    timeout = float(cfg.get("timeout_s", 60))

    router = APIRouter()

    @router.get("/panel")
    async def _panel():
        return HTMLResponse(_MINIMAL_PAGE if mode == "minimal" else _FULL_PAGE)

    # The minimal-mode shot/nav DATA routes moved to build_panel_data_router —
    # gated under /api/plugins/agent_browser (plugin-view rule 2). The /panel/dash
    # reverse proxy below stays on the public prefix OF NECESSITY: it's loaded by
    # an <iframe>, and an iframe navigation can't carry an Authorization bearer.
    # Gating it needs a designed handoff (tracked upstream) — same posture as the
    # page itself.

    # ── full-mode backing route: same-origin reverse proxy to the dashboard ────
    # The dashboard daemon binds the SERVING box's loopback (127.0.0.1:<port>).
    # Proxying it through our own router makes the embed same-origin (so it rides
    # the fleet proxy + the postMessage handshake works) instead of an absolute
    # http://localhost:<port> the operator's browser would resolve against ITS
    # OWN machine (issue #6 — unreachable for a member). Streaming-safe; upgrades
    # WebSockets (the dashboard's live viewport/feed channels) when reachable.
    _DASH = f"http://127.0.0.1:{port}"
    # Hop-by-hop headers must not cross a proxy boundary (RFC 7230 §6.1).
    _HOP = {"host", "content-length", "connection", "keep-alive", "transfer-encoding",
            "te", "trailer", "upgrade", "proxy-authorization", "proxy-authenticate"}

    @router.get("/panel/dash")
    @router.get("/panel/dash/{path:path}")
    async def _dash(request: Request, path: str = ""):
        """HTTP reverse proxy → the local dashboard daemon (same-origin embed).
        Streams the upstream response unbuffered (SSE-safe); 502 when the daemon
        isn't up (e.g. ``agent-browser dashboard start`` hasn't run yet)."""
        try:
            import httpx
        except Exception:  # noqa: BLE001 — httpx is a host dep; degrade if somehow absent
            return Response(status_code=502, content="dashboard proxy unavailable (httpx missing)")
        url = f"{_DASH}/{path}" if path else _DASH
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
        body = await request.body()
        client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5.0))
        try:
            upstream_req = client.build_request(
                request.method, url, headers=headers, content=body,
                params=dict(request.query_params))
            upstream = await client.send(upstream_req, stream=True)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            await client.aclose()
            return Response(status_code=502,
                            content="dashboard not reachable — run `agent-browser dashboard start`")
        except Exception:  # noqa: BLE001
            await client.aclose()
            log.warning("[agent_browser] dashboard proxy error", exc_info=True)
            return Response(status_code=502, content="dashboard proxy error")

        async def _pipe():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP}
        return StreamingResponse(_pipe(), status_code=upstream.status_code, headers=resp_headers)

    @router.websocket("/panel/dash")
    @router.websocket("/panel/dash/{path:path}")
    async def _dash_ws(websocket: WebSocket, path: str = ""):
        """WebSocket reverse proxy → the dashboard's live channels (CDP screencast /
        activity feeds). Bidirectional pump; both halves close together. Works on the
        host directly; over the fleet proxy it depends on the hub forwarding upgrades."""
        try:
            import websockets
            from websockets.exceptions import ConnectionClosed
        except Exception:  # noqa: BLE001 — websockets is a host dep
            await websocket.close(code=1011)
            return
        scheme = "ws"
        target = f"{scheme}://127.0.0.1:{port}/{path}" if path else f"{scheme}://127.0.0.1:{port}"
        qs = websocket.url.query
        if qs:
            target = f"{target}?{qs}"
        subprotocols = websocket.scope.get("subprotocols") or None
        await websocket.accept()
        try:
            async with websockets.connect(target, subprotocols=subprotocols,
                                           open_timeout=5, max_size=None) as upstream:
                async def _c2s():
                    try:
                        while True:
                            msg = await websocket.receive()
                            if msg.get("type") == "websocket.disconnect":
                                break
                            if (t := msg.get("text")) is not None:
                                await upstream.send(t)
                            elif (b := msg.get("bytes")) is not None:
                                await upstream.send(b)
                    except Exception:  # noqa: BLE001
                        pass

                async def _s2c():
                    try:
                        async for data in upstream:
                            if isinstance(data, (bytes, bytearray)):
                                await websocket.send_bytes(bytes(data))
                            else:
                                await websocket.send_text(data)
                    except (ConnectionClosed, Exception):  # noqa: BLE001
                        pass

                done, pending = await asyncio.wait(
                    {asyncio.create_task(_c2s()), asyncio.create_task(_s2c())},
                    return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
        except Exception:  # noqa: BLE001 — daemon down / handshake failed
            log.info("[agent_browser] dashboard ws proxy: upstream not reachable", exc_info=True)
        finally:
            try:
                await websocket.close()
            except Exception:  # noqa: BLE001
                pass

    return router


def build_panel_data_router(cfg: dict | None):
    """The minimal-mode DATA/ACTION routes — mounted under
    ``/api/plugins/agent_browser`` so they inherit the operator bearer gate
    (plugin-view rule 2). Previously ``/panel/shot`` + ``POST /panel/nav`` lived
    under the public ``/plugins/`` prefix: on a token-gated deployment anyone who
    could reach the port could DRIVE the operator's browser session and read its
    screen without the bearer."""
    cfg = cfg or {}
    binary = str(cfg.get("binary") or "agent-browser")
    timeout = float(cfg.get("timeout_s", 60))

    router = APIRouter()

    def _run(*args: str) -> tuple[int, str]:
        try:
            p = subprocess.run([binary, *args], capture_output=True, text=True, timeout=timeout)
            return p.returncode, (p.stderr or p.stdout or "").strip()
        except FileNotFoundError:
            return 127, f"{binary!r} not on PATH"
        except subprocess.TimeoutExpired:
            return 124, "timed out"

    @router.get("/shot")
    async def _shot():
        """Latest viewport as a PNG. Throttled to ~1/0.8s so a fast poller can't
        spawn a screenshot subprocess per frame."""
        global _shot_ts
        now = time.monotonic()
        if now - _shot_ts > 0.8 or not os.path.exists(_SHOT_PATH):
            rc, err = await asyncio.to_thread(lambda: _run("screenshot", _SHOT_PATH))
            if rc == 0 and os.path.exists(_SHOT_PATH):
                _shot_ts = now
            else:
                return Response(status_code=503, content=f"no frame: {err[:200]}")
        return FileResponse(_SHOT_PATH, media_type="image/png",
                            headers={"Cache-Control": "no-store"})

    @router.post("/nav")
    async def _nav(body: dict = Body(...)):
        """Drive the viewport from the toolbar: open <url> / back / forward / reload."""
        action = str(body.get("action", "")).strip().lower()
        url = str(body.get("url", "")).strip()
        if action == "open":
            if not url:
                return JSONResponse({"ok": False, "error": "url required"})
            rc, err = await asyncio.to_thread(lambda: _run("open", url))
        elif action in ("back", "forward", "reload"):
            rc, err = await asyncio.to_thread(lambda: _run(action))
        else:
            return JSONResponse({"ok": False, "error": f"bad action {action!r}"})
        return JSONResponse({"ok": rc == 0, "error": "" if rc == 0 else err[:200]})

    return router


# ── full mode: iframe agent-browser's dashboard (same-origin reverse proxy) ────
# Chrome is the protoLabs design system: link the no-build plugin-kit (--pl-* tokens
# + .pl-* components), drive theming from the console handshake (ADR 0038). Only the
# ~30px wrapper bar is ours; the viewport is agent-browser's third-party dashboard.
#
# The iframe src is derived SAME-ORIGIN + SLUG-AWARE (issue #6): the dashboard is
# served through our own /panel/dash reverse proxy, never a hardcoded
# http://localhost:PORT (which the operator's browser resolves against its OWN box —
# unreachable for a member, and cross-origin breaks the postMessage handshake).
#   base = location.pathname.split("/plugins/")[0]   (="" on host, /agents/<slug> proxied)
#   src  = base + "/plugins/agent_browser/panel/dash/"
_FULL_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Browser</title>
<link id="dskit" rel="stylesheet" href="">
<style>
  html,body{margin:0;height:100%;background:var(--pl-color-bg);font-family:var(--pl-font-sans)}
  .bar{height:30px;display:flex;align-items:center;gap:8px;padding:0 12px;color:var(--pl-color-fg-muted);
    font-size:11.5px;border-bottom:var(--pl-border-width) solid var(--pl-color-border)}
  .bar b{color:var(--pl-color-accent)} a{color:var(--pl-color-accent)}
  iframe{display:block;width:100%;height:calc(100% - 30px);border:0;background:var(--pl-color-bg)}
</style></head><body>
<script>
// Same-origin base: "" on the host, "/agents/<slug>" when served through the fleet
// proxy. Prefix EVERY asset / iframe-src / link with it — never hardcode an absolute
// path or a localhost:PORT origin (issue #6 — breaks the proxy + the token handshake).
const BASE=location.pathname.split("/plugins/")[0];
document.getElementById("dskit").href=BASE+"/_ds/plugin-kit.css";   // DS kit, same-origin
const DASH=BASE+"/plugins/agent_browser/panel/dash/";                // reverse-proxied dashboard
</script>
  <div class="bar"><b>Browser</b><span>agent-browser dashboard
    · <a id="dashlink" href="#" target="_blank">open</a></span>
    <span style="margin-left:auto">run <code>agent-browser dashboard start</code> if blank</span></div>
  <iframe id="f"></iframe>
<script type="module">
document.getElementById("dashlink").href=DASH;
document.getElementById("f").src=DASH;
// The DS plugin-kit owns theming THIS page's chrome (the handshake + live
// re-themes onto --pl-*); it's an ES MODULE, so dynamic import (a classic
// <script src> throws on its exports). The thin relay below stays: the embedded
// third-party dashboard wants the RAW console message, which the kit doesn't
// re-broadcast.
try { (await import(BASE + "/_ds/plugin-kit.js")).initPluginView(); } catch (e) {}
window.addEventListener("message",(e)=>{const d=e.data||{};
  if(d.type==="protoagent:init"||d.type==="protoagent:theme"){
    const f=document.getElementById("f"); try{f.contentWindow.postMessage(d,"*")}catch(_){} }});
</script></body></html>"""


# ── minimal mode: viewport-only (screenshot-poll + nav toolbar) ────────────────
# Chrome is the protoLabs design system: plugin-kit CSS + .pl-* components (nav as
# .pl-btn, url as .pl-input, empty state as .pl-empty), themed live by the handshake.
_MINIMAL_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Browser</title>
<link id="dskit" rel="stylesheet" href="">
<script>
// Same-origin base: "" on the host, "/agents/<slug>" when served through the fleet
// proxy. Prefix EVERY fetch / asset with it — never hardcode an absolute path.
const BASE=location.pathname.split("/plugins/")[0];
document.getElementById("dskit").href=BASE+"/_ds/plugin-kit.css";
</script>
<style>
  html,body{margin:0;height:100%;background:var(--pl-color-bg);color:var(--pl-color-fg);
    font-family:var(--pl-font-sans);font-size:13px}
  .bar{height:38px;display:flex;align-items:center;gap:6px;padding:0 10px;
    border-bottom:var(--pl-border-width) solid var(--pl-color-border)}
  .bar .pl-input{flex:1;min-width:0}
  .stage{height:calc(100% - 38px);display:flex;align-items:flex-start;justify-content:center;
    overflow:auto;background:var(--pl-color-bg-inset)}
  img{max-width:100%;display:block}
  .stage .pl-empty{margin:auto}
</style></head><body>
  <div class="bar">
    <button class="pl-btn pl-btn--ghost pl-btn--icon pl-btn--sm" title="Back" onclick="nav('back')">◀</button>
    <button class="pl-btn pl-btn--ghost pl-btn--icon pl-btn--sm" title="Forward" onclick="nav('forward')">▶</button>
    <button class="pl-btn pl-btn--ghost pl-btn--icon pl-btn--sm" title="Reload" onclick="nav('reload')">⟳</button>
    <input id="url" class="pl-input" placeholder="example.com — Enter to open" autocomplete="off">
    <button class="pl-btn pl-btn--primary pl-btn--sm" onclick="go()">Go</button>
  </div>
  <div class="stage"><img id="screen" alt="">
    <div id="hint" class="pl-empty pl-empty--slotted">
      <div class="pl-empty__title">No page loaded</div>
      <div class="pl-empty__desc">Open a URL above, or let the agent drive — <code>browser_open</code>.</div>
    </div>
  </div>
<script type="module">
// The DS plugin-kit owns the protoagent:init handshake (bearer + theme, incl. live
// re-themes onto the --pl-* tokens) and slug-aware authed fetches — replacing the
// hand-rolled TMAP/listener this page carried. plugin-kit.js is an ES MODULE, so it
// loads via dynamic import (a classic <script src> throws on its exports; see
// protoAgent docs/how-to/build-a-plugin-view.md). Older host without /_ds: fall
// back to a tokenless same-origin shim.
let kit;
try { kit = await import(BASE + "/_ds/plugin-kit.js"); }
catch (e) { kit = { initPluginView(){}, apiFetch: (p, i) => fetch(BASE + p, i) }; }
const $=(id)=>document.getElementById(id);
$("url").addEventListener("keydown",(e)=>{if(e.key==="Enter")go()});

async function nav(action,url){
  try{ await kit.apiFetch("/api/plugins/agent_browser/nav",{method:"POST",
    headers:{"Content-Type":"application/json"},body:JSON.stringify({action,url})});
  }catch(_){}
  setTimeout(refresh,400);
}
function go(){ let u=$("url").value.trim(); if(!u)return; if(!/^https?:\/\//.test(u))u="https://"+u; nav("open",u); }
// Module scripts are scoped — expose the toolbar's inline onclick handlers.
window.nav=nav; window.go=go;

let busy=false;
async function refresh(){
  if(busy)return; busy=true;
  try{
    const r=await kit.apiFetch("/api/plugins/agent_browser/shot?t="+Date.now());
    if(r.ok){ const b=await r.blob(); const u=URL.createObjectURL(b);
      const img=$("screen"); const old=img.src; img.src=u; $("hint").style.display="none";
      if(old&&old.startsWith("blob:"))URL.revokeObjectURL(old); }
  }catch(_){}
  busy=false;
}
// Boot ONCE, on whichever fires first: the handshake (the bearer arrives with
// protoagent:init, so the gated shot/nav calls authenticate) or a short timer
// for the no-handshake case (standalone page / older host).
let booted=false;
function boot(){ if(booted)return; booted=true; refresh(); setInterval(refresh,1200); }
kit.initPluginView(boot);
setTimeout(boot, 800);
</script></body></html>"""
