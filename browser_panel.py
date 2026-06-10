"""Browser panel console view (ADR 0026) — two modes, set by ``panel_mode``.

- ``full`` (default): iframes agent-browser's own live dashboard (viewport +
  activity/console/network/… feeds), reusing their renderer wholesale.
- ``minimal``: a viewport-only page we render ourselves — a live screenshot (polled
  from the CLI, same-origin, no WS-protocol dependency) plus a slim nav toolbar
  (url / back / forward / reload). "Expose less": just the page, nothing else.

Self-contained vanilla JS (no build step). The page reads/drives the browser only
through the CLI behind same-origin routes; the agent's own browser_* tools are the
primary driver — this is an operator viewport.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import time

_SHOT_PATH = os.path.join(tempfile.gettempdir(), "agent_browser_panel.png")
_shot_ts = 0.0  # last successful capture (cheap throttle so polling can't storm the CLI)


def build_panel_router(cfg: dict | None):
    from fastapi import APIRouter, Body
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

    cfg = cfg or {}
    port = int(cfg.get("dashboard_port", 4848))
    mode = str(cfg.get("panel_mode", "full")).strip().lower()
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

    @router.get("/panel")
    async def _panel():
        return HTMLResponse(_MINIMAL_PAGE if mode == "minimal" else _full_page(port))

    # ── minimal-mode backing routes (same-origin) ─────────────────────────────
    @router.get("/panel/shot")
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

    @router.post("/panel/nav")
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


def _full_page(port: int) -> str:
    return _FULL_PAGE.replace("__PORT__", str(port))


# ── full mode: iframe agent-browser's dashboard ───────────────────────────────
# Chrome is the protoLabs design system: link the no-build plugin-kit (--pl-* tokens
# + .pl-* components), drive theming from the console handshake (ADR 0038). Only the
# ~30px wrapper bar is ours; the viewport is agent-browser's third-party dashboard.
_FULL_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Browser</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@protolabsai/ui@0.22/dist/plugin-kit.css">
<style>
  html,body{margin:0;height:100%;background:var(--pl-color-bg);font-family:var(--pl-font-sans)}
  .bar{height:30px;display:flex;align-items:center;gap:8px;padding:0 12px;color:var(--pl-color-fg-muted);
    font-size:11.5px;border-bottom:var(--pl-border-width) solid var(--pl-color-border)}
  .bar b{color:var(--pl-color-accent)} a{color:var(--pl-color-accent)}
  iframe{display:block;width:100%;height:calc(100% - 30px);border:0;background:var(--pl-color-bg)}
</style></head><body>
  <div class="bar"><b>Browser</b><span>agent-browser dashboard · <a href="http://localhost:__PORT__" target="_blank">:__PORT__</a></span>
    <span style="margin-left:auto">run <code>agent-browser dashboard start</code> if blank</span></div>
  <iframe id="f" src="http://localhost:__PORT__"></iframe>
<script>
// ADR 0038 handshake — map the console's curated theme onto --pl-* tokens, and
// forward the message to agent-browser's inner dashboard.
const TMAP={bg:["--pl-color-bg"],bgPanel:["--pl-color-bg-raised","--pl-color-bg-subtle"],
  fg:["--pl-color-fg"],fgMuted:["--pl-color-fg-muted"],brand:["--pl-color-accent"],border:["--pl-color-border"]};
let TOKEN=null;
function applyTheme(t){const r=document.documentElement;for(const[k,v] of Object.entries(t||{}))
  (TMAP[k]||(k.startsWith("--pl-")?[k]:[])).forEach(p=>v&&r.style.setProperty(p,v));}
window.addEventListener("message",(e)=>{const d=e.data||{};
  if(d.type==="protoagent:init"){ if(d.token)TOKEN=d.token; applyTheme(d.theme);
    const f=document.getElementById("f"); try{f.contentWindow.postMessage(d,"*")}catch(_){} }
  else if(d.type==="protoagent:theme"){ applyTheme(d.theme);
    const f=document.getElementById("f"); try{f.contentWindow.postMessage(d,"*")}catch(_){} }});
</script></body></html>"""


# ── minimal mode: viewport-only (screenshot-poll + nav toolbar) ────────────────
# Chrome is the protoLabs design system: plugin-kit CSS + .pl-* components (nav as
# .pl-btn, url as .pl-input, empty state as .pl-empty), themed live by the handshake.
_MINIMAL_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Browser</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@protolabsai/ui@0.22/dist/plugin-kit.css">
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
<script>
// ADR 0038 handshake — map the console's curated theme onto --pl-* tokens.
const TMAP={bg:["--pl-color-bg"],bgPanel:["--pl-color-bg-raised","--pl-color-bg-subtle"],
  fg:["--pl-color-fg"],fgMuted:["--pl-color-fg-muted"],brand:["--pl-color-accent"],border:["--pl-color-border"]};
let TOKEN=null;
function applyTheme(t){const r=document.documentElement;for(const[k,v] of Object.entries(t||{}))
  (TMAP[k]||(k.startsWith("--pl-")?[k]:[])).forEach(p=>v&&r.style.setProperty(p,v));}
window.addEventListener("message",(e)=>{const d=e.data||{};
  if(d.type==="protoagent:init"){if(d.token)TOKEN=d.token;applyTheme(d.theme);}
  else if(d.type==="protoagent:theme")applyTheme(d.theme);});
const H=()=>TOKEN?{Authorization:"Bearer "+TOKEN}:{};
const $=(id)=>document.getElementById(id);
$("url").addEventListener("keydown",(e)=>{if(e.key==="Enter")go()});

async function nav(action,url){
  try{ await fetch("/plugins/agent_browser/panel/nav",{method:"POST",
    headers:{"Content-Type":"application/json",...H()},body:JSON.stringify({action,url})});
  }catch(_){}
  setTimeout(refresh,400);
}
function go(){ let u=$("url").value.trim(); if(!u)return; if(!/^https?:\/\//.test(u))u="https://"+u; nav("open",u); }

let busy=false;
async function refresh(){
  if(busy)return; busy=true;
  try{
    const r=await fetch("/plugins/agent_browser/panel/shot?t="+Date.now(),{headers:H()});
    if(r.ok){ const b=await r.blob(); const u=URL.createObjectURL(b);
      const img=$("screen"); const old=img.src; img.src=u; $("hint").style.display="none";
      if(old&&old.startsWith("blob:"))URL.revokeObjectURL(old); }
  }catch(_){}
  busy=false;
}
refresh(); setInterval(refresh,1200);   // ~live; the agent or the toolbar moves the page under us
</script></body></html>"""
