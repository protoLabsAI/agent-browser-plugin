"""Browser panel console view (ADR 0026) — two modes, set by ``panel_mode``.

- ``minimal`` (default): a viewport-only page we render ourselves — a live screenshot
  (polled from the CLI through the gated /shot route, same-origin, no WS/proxy
  dependency) + a nav toolbar (url / back / forward / reload) + a Dashboard control
  (start/stop/status). Works everywhere — host and member alike.
- ``full``: a launcher for agent-browser's own dashboard (viewport + activity/console/
  network feeds). We deliberately do NOT embed it: that dashboard is a prebuilt Next.js
  app whose assets are ROOT-ABSOLUTE (``/_next/…``) with no base-path option, so it can't
  render under a sub-path reverse proxy (the long-standing blank panel) — it only renders
  at its OWN origin. Full mode opens it there ("Open dashboard ↗"), which works on a
  local/host setup; for a remote member, use minimal.

Self-contained vanilla JS (no build step). Every fetch / link is derived from
``base = location.pathname.split("/plugins/")[0]`` (="" on the host, ``/agents/<slug>``
when proxied) so the page is same-origin + slug-aware (the token/theme handshake). The
browser is driven only through the CLI behind same-origin routes; the agent's own
browser_* tools are the primary driver — this is an operator viewport.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
import time

# Module-scope fastapi imports (the host always provides fastapi; __init__.py catches
# ImportError so the tools still serve if the panel can't import).
from fastapi import APIRouter, Body
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

log = logging.getLogger("protoagent.plugins.agent_browser")

_SHOT_PATH = os.path.join(tempfile.gettempdir(), "agent_browser_panel.png")
_shot_ts = 0.0  # last successful capture (cheap throttle so polling can't storm the CLI)


def build_panel_router(cfg: dict | None):

    cfg = cfg or {}
    port = int(cfg.get("dashboard_port", 4848))
    mode = str(cfg.get("panel_mode", "minimal")).strip().lower()

    router = APIRouter()

    @router.get("/panel")
    async def _panel():
        page = _MINIMAL_PAGE if mode == "minimal" else _FULL_PAGE
        return HTMLResponse(page.replace("__DASH_PORT__", str(port)))

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
    port = int(cfg.get("dashboard_port", 4848))

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

    # ── dashboard control (start it up entirely from the panel UI) ──────────────
    # The agent-browser dashboard is a standalone daemon. These let the panel show its
    # status and start/stop it without dropping to a terminal. Status is a live probe of
    # the daemon's loopback port (there's no `dashboard status` subcommand).
    @router.get("/dashboard")
    async def _dash_status():
        try:
            import httpx

            async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as c:
                await c.get(f"http://127.0.0.1:{port}/")
            running = True
        except Exception:  # noqa: BLE001 — connection refused / down → not running
            running = False
        return JSONResponse({"running": running, "port": port})

    @router.post("/dashboard")
    async def _dash_control(body: dict = Body(...)):
        """Start or stop the dashboard daemon from the UI. `action` is start|stop."""
        action = str(body.get("action", "")).strip().lower()
        if action == "start":
            rc, err = await asyncio.to_thread(lambda: _run("dashboard", "start", "--port", str(port)))
        elif action == "stop":
            rc, err = await asyncio.to_thread(lambda: _run("dashboard", "stop"))
        else:
            return JSONResponse({"ok": False, "error": f"action must be start|stop, got {action!r}"})
        return JSONResponse({"ok": rc == 0, "error": "" if rc == 0 else err[:200], "port": port})

    return router


# ── full mode: a LAUNCHER for agent-browser's dashboard (not an embed) ──────────
# The dashboard is a prebuilt Next.js app whose assets are root-absolute (/_next/…)
# with no base-path option, so it can't render under our sub-path panel — it only
# loads at its OWN origin. So full mode is a launcher: a Start/Stop control + an
# "Open dashboard ↗" link to that origin (reachable on a local/host setup). For a
# remote member, minimal mode is the one that works.
_FULL_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Browser</title>
<link id="dskit" rel="stylesheet" href="">
<script>
const BASE=location.pathname.split("/plugins/")[0];
document.getElementById("dskit").href=BASE+"/_ds/plugin-kit.css";
const DASH_PORT=__DASH_PORT__;
function dashOpenUrl(){ return "http://"+location.hostname+":"+DASH_PORT+"/"; }
</script>
<style>
  html,body{margin:0;height:100%;background:var(--pl-color-bg);color:var(--pl-color-fg);
    font-family:var(--pl-font-sans);font-size:13px}
  .bar{height:38px;display:flex;align-items:center;gap:8px;padding:0 12px;
    border-bottom:var(--pl-border-width) solid var(--pl-color-border)}
  .bar b{color:var(--pl-color-accent)}
  .dot{width:7px;height:7px;border-radius:50%;display:inline-block;flex:none}
  .dot.ok{background:#22c55e}.dot.off{background:var(--pl-color-fg-muted,#9aa0aa)}
  .stage{height:calc(100% - 38px);display:flex;align-items:center;justify-content:center;
    padding:24px;background:var(--pl-color-bg-inset);box-sizing:border-box}
  .card{max-width:440px;text-align:center}
  .card .t{font-size:15px;font-weight:600;margin-bottom:8px}
  .card .d{color:var(--pl-color-fg-muted);line-height:1.6;margin-bottom:16px}
  .card .tip{margin-top:16px;font-size:11.5px;color:var(--pl-color-fg-muted)}
  .card code{background:var(--pl-color-bg-subtle,rgba(127,127,127,.14));padding:.1em .35em;border-radius:4px}
</style></head><body>
  <div class="bar"><b>Browser</b><span id="dash" title="agent-browser dashboard"></span></div>
  <div class="stage"><div class="card">
    <div class="t">agent-browser dashboard</div>
    <div class="d">The full dashboard — live viewport plus activity, console, and network
      feeds — runs in its own window. It can't embed in this panel (its assets load from the
      page root, which a sub-path panel can't serve), so it opens in a new tab.</div>
    <a id="openlink" class="pl-btn pl-btn--primary" href="" target="_blank" rel="noopener">Open dashboard ↗</a>
    <div class="tip">Want the browser <em>inside</em> the console? Set
      <code>panel_mode: minimal</code> — a live viewport you can drive right here.</div>
  </div></div>
<script type="module">
document.getElementById("openlink").href=dashOpenUrl();
let kit;
try { kit = await import(BASE + "/_ds/plugin-kit.js"); kit.initPluginView(); }
catch (e) { kit = { initPluginView(){}, apiFetch: (p, i) => fetch(BASE + p, i) }; }
// Dashboard control — start/stop the daemon from the panel (no terminal).
function renderDash(running){
  const el=document.getElementById("dash"); if(running===null){ el.innerHTML=""; return; }
  el.innerHTML = running
    ? '<span class="dot ok"></span> running <button class="pl-btn pl-btn--ghost pl-btn--sm" onclick="dashAct(\'stop\')">Stop</button>'
    : '<span class="dot off"></span> stopped <button class="pl-btn pl-btn--sm" onclick="dashAct(\'start\')">Start dashboard</button>';
}
async function dashStatus(){
  try{ const r=await kit.apiFetch("/api/plugins/agent_browser/dashboard"); const d=await r.json();
    renderDash(!!d.running); }catch(_){ renderDash(null); }
}
async function dashAct(action){
  document.getElementById("dash").innerHTML='<span class="dot off"></span> …';
  try{ await kit.apiFetch("/api/plugins/agent_browser/dashboard",{method:"POST",
    headers:{"Content-Type":"application/json"},body:JSON.stringify({action})}); }catch(_){}
  setTimeout(dashStatus, 900);
}
window.dashAct=dashAct;
dashStatus(); setInterval(dashStatus, 6000);
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
  .dash{display:flex;align-items:center;gap:4px;font-size:11px;color:var(--pl-color-fg-muted);margin-left:4px}
  .dot{width:7px;height:7px;border-radius:50%;display:inline-block;flex:none}
  .dot.ok{background:#22c55e}.dot.off{background:var(--pl-color-fg-muted,#9aa0aa)}
</style></head><body>
  <div class="bar">
    <button class="pl-btn pl-btn--ghost pl-btn--icon pl-btn--sm" title="Back" onclick="nav('back')">◀</button>
    <button class="pl-btn pl-btn--ghost pl-btn--icon pl-btn--sm" title="Forward" onclick="nav('forward')">▶</button>
    <button class="pl-btn pl-btn--ghost pl-btn--icon pl-btn--sm" title="Reload" onclick="nav('reload')">⟳</button>
    <input id="url" class="pl-input" placeholder="example.com — Enter to open" autocomplete="off">
    <button class="pl-btn pl-btn--primary pl-btn--sm" onclick="go()">Go</button>
    <span id="dash" class="dash" title="agent-browser dashboard"></span>
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
// Dashboard control — start/stop the agent-browser dashboard daemon from the UI (no
// terminal), show its status, and link out to it. The rich dashboard can't be EMBEDDED
// through our sub-path proxy (its Next.js assets are root-absolute), so "Dashboard ↗"
// opens it at its own origin in a new tab — which works on a local/host setup.
var dashPort=__DASH_PORT__;
function dashOpenUrl(){ return "http://"+location.hostname+":"+dashPort+"/"; }
async function dashStatus(){
  try{ var r=await kit.apiFetch("/api/plugins/agent_browser/dashboard"); var d=await r.json();
    dashPort=d.port||dashPort; renderDash(!!d.running); }catch(_){ renderDash(null); }
}
function renderDash(running){
  var el=$("dash"); if(running===null){ el.innerHTML=""; return; }
  var open='<a class="pl-btn pl-btn--ghost pl-btn--sm" href="'+dashOpenUrl()+'" target="_blank" rel="noopener" title="Open the full dashboard (viewport + activity/console/network feeds) in a new tab">Dashboard ↗</a>';
  el.innerHTML = running
    ? '<span class="dot ok"></span>'+open+'<button class="pl-btn pl-btn--ghost pl-btn--sm" onclick="dashAct(\'stop\')" title="Stop the dashboard daemon">Stop</button>'
    : '<span class="dot off"></span><button class="pl-btn pl-btn--sm" onclick="dashAct(\'start\')" title="Start the agent-browser dashboard daemon">Start dashboard</button>';
}
async function dashAct(action){
  var el=$("dash"); el.innerHTML='<span class="dot off"></span>…';
  try{ await kit.apiFetch("/api/plugins/agent_browser/dashboard",{method:"POST",
    headers:{"Content-Type":"application/json"},body:JSON.stringify({action})}); }catch(_){}
  setTimeout(dashStatus,800);
}
window.dashAct=dashAct;

// Boot ONCE, on whichever fires first: the handshake (the bearer arrives with
// protoagent:init, so the gated shot/nav calls authenticate) or a short timer
// for the no-handshake case (standalone page / older host).
let booted=false;
function boot(){ if(booted)return; booted=true; refresh(); setInterval(refresh,1200); dashStatus(); setInterval(dashStatus,6000); }
kit.initPluginView(boot);
setTimeout(boot, 800);
</script></body></html>"""
