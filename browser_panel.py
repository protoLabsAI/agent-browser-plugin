"""Browser panel console view (ADR 0026) — embeds agent-browser's live dashboard.

A self-contained page served at ``/plugins/agent_browser/panel`` that iframes the
agent-browser dashboard (viewport + activity/console/network feeds).  The console
renders a left-rail icon (manifest ``views:``) whose panel loads this page; on load
the console ``postMessage``s a bearer token + theme tokens (the ADR 0026 handshake),
which the page forwards into the iframe for authenticated dashboard access.

No build step — vanilla JS, no frameworks, so the whole plugin stays a drop-in
package.  The page is a thin viewer; it never mutates browser state (mutation stays
with the tools + the CLI).
"""

from __future__ import annotations


def build_panel_router(cfg: dict | None):
    """A FastAPI router for the browser panel page.

    Reads ``dashboard_port`` from *cfg* (default 4848) and bakes it into the
    iframe ``src`` of the served HTML.
    """
    from fastapi import APIRouter
    from fastapi.responses import HTMLResponse

    cfg = cfg or {}
    port = int(cfg.get("dashboard_port", 4848))

    router = APIRouter()

    _PAGE = _build_page(port)

    @router.get("/panel")
    async def _panel():
        return HTMLResponse(_PAGE)

    return router


def _build_page(port: int) -> str:
    dashboard_url = f"http://localhost:{port}"
    return rf"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Browser Dashboard</title>
<style>
  :root{{
    --bg:#0a0a0c; --raised:#141418; --border:#26262d; --fg:#ededed;
    --fg-muted:#8b8b95; --accent:#a78bfa;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  html,body{{height:100%;background:var(--bg);color:var(--fg);
    font-family:ui-sans-serif,system-ui,-apple-system,sans-serif;font-size:13px}}
  .topbar{{display:flex;align-items:center;gap:10px;height:36px;padding:0 14px;
    background:var(--raised);border-bottom:1px solid var(--border);font-size:12px}}
  .topbar .url{{color:var(--accent);font-family:ui-monospace,monospace;font-size:11.5px}}
  .topbar .hint{{color:var(--fg-muted);margin-left:auto;font-size:11px}}
  iframe{{display:block;width:100%;height:calc(100% - 36px);border:0;background:var(--bg)}}
  .fallback{{display:none;position:absolute;inset:36px 0 0 0;
    align-items:center;justify-content:center;color:var(--fg-muted);font-size:14px}}
</style></head><body>
  <div class="topbar">
    <span class="url">{dashboard_url}</span>
    <span class="hint">Dashboard started at {dashboard_url} must be running</span>
  </div>
  <iframe id="dash" src="{dashboard_url}" allow="clipboard-read;clipboard-write"></iframe>
  <div id="fallback" class="fallback">Unable to load dashboard — is agent-browser running?</div>
<script>
// ── ADR 0026 handshake: bearer token + theme tokens from the console.
let TOKEN = null;
window.addEventListener("message", (e) => {{
  const d = e.data || {{}};
  if (d.type === "protoagent:init") {{
    if (d.token) TOKEN = d.token;
    if (d.theme) for (const [k, v] of Object.entries(d.theme)) {{
      if (k.includes("bg")) document.documentElement.style.setProperty("--bg", v);
      if (k.includes("accent")) document.documentElement.style.setProperty("--accent", v);
      if (k.includes("fg")) document.documentElement.style.setProperty("--fg", v);
      if (k.includes("border")) document.documentElement.style.setProperty("--border", v);
      if (k.includes("raised")) document.documentElement.style.setProperty("--raised", v);
      if (k.includes("muted")) document.documentElement.style.setProperty("--fg-muted", v);
    }}
  }}
}});

// Forward auth token into the iframe on load (same-origin postMessage).
const frame = document.getElementById("dash");
frame.addEventListener("load", () => {{
  if (TOKEN) frame.contentWindow.postMessage({{ type: "auth", token: TOKEN }}, "*");
}});

// Show fallback banner if the iframe fails to connect.
frame.addEventListener("error", () => {{ document.getElementById("fallback").style.display = "flex"; }});
</script></body></html>"""
