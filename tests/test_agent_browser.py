"""Tests for the agent_browser plugin — the tool subprocess wrappers (arg-building +
graceful error degradation), the interactive panel routes (page / ticket / WS gating /
nav), register() wiring, and manifest/version coherence. Host-free: subprocess.run is
mocked, so no agent-browser binary and no real browser are needed."""

from __future__ import annotations

from pathlib import Path

import pytest

import agent_browser.browser_panel as bp
import agent_browser.tools as tools
from conftest import fake_run

ROOT = Path(__file__).resolve().parent.parent


def _toolmap(cfg=None):
    return {t.name: t for t in tools.get_browser_tools(cfg or {})}


# ── the tools: arg-building ──────────────────────────────────────────────────────


async def test_open_passes_url_and_curated_launch_flags(monkeypatch):
    rec = []
    monkeypatch.setattr(tools.subprocess, "run", fake_run(stdout="OPENED", record=rec))
    t = _toolmap({"binary": "ab", "headed": True, "allowed_domains": "x.com", "max_output": 500})
    out = await t["browser_open"].ainvoke({"url": "https://x.com"})
    assert "OPENED" in out
    assert rec[-1] == ["ab", "--headed", "--allowed-domains", "x.com", "--max-output", "500", "open", "https://x.com"]


async def test_open_blank_url_omits_it(monkeypatch):
    rec = []
    monkeypatch.setattr(tools.subprocess, "run", fake_run(record=rec))
    await _toolmap({"binary": "ab"})["browser_open"].ainvoke({})
    assert rec[-1] == ["ab", "open"]


async def test_action_tools_pass_refs(monkeypatch):
    rec = []
    monkeypatch.setattr(tools.subprocess, "run", fake_run(record=rec))
    t = _toolmap({"binary": "ab"})
    await t["browser_click"].ainvoke({"selector": "@e2"})
    assert rec[-1] == ["ab", "click", "@e2"]
    await t["browser_fill"].ainvoke({"selector": "#q", "text": "hi there"})
    assert rec[-1] == ["ab", "fill", "#q", "hi there"]
    await t["browser_snapshot"].ainvoke({})
    assert rec[-1] == ["ab", "snapshot"]


def test_all_16_tools_present():
    names = set(_toolmap())
    assert len(names) == 16
    assert {
        "browser_open",
        "browser_snapshot",
        "browser_click",
        "browser_fill",
        "browser_screenshot",
        "browser_eval",
        "browser_close",
    } <= names
    assert "browser_dashboard" not in names  # the dashboard tool is gone (full switchover)


# ── the tools: graceful error degradation (a failed action informs, never crashes) ──


async def test_missing_binary_returns_install_hint(monkeypatch):
    def boom(args, **kw):
        raise FileNotFoundError()

    monkeypatch.setattr(tools.subprocess, "run", boom)
    out = await _toolmap({"binary": "nope"})["browser_snapshot"].ainvoke({})
    assert "not on PATH" in out and "npm i -g agent-browser" in out


async def test_timeout_returns_readable_error(monkeypatch):
    def slow(args, **kw):
        raise tools.subprocess.TimeoutExpired(cmd="ab", timeout=1)

    monkeypatch.setattr(tools.subprocess, "run", slow)
    out = await _toolmap({"binary": "ab", "timeout_s": 1})["browser_snapshot"].ainvoke({})
    assert "timed out" in out


async def test_nonzero_exit_surfaces_stderr(monkeypatch):
    monkeypatch.setattr(tools.subprocess, "run", fake_run(rc=2, stderr="boom"))
    out = await _toolmap({"binary": "ab"})["browser_click"].ainvoke({"selector": "@e9"})
    assert out.startswith("Error:") and "boom" in out


# ── register() wiring ────────────────────────────────────────────────────────────


def test_register_wires_tools_and_panel_routers(registry):
    import agent_browser as pkg

    pkg.register(registry)
    assert len(registry.tools) == 16
    prefixes = [p for p, _ in registry.routers]
    assert None in prefixes  # the panel PAGE (host default prefix /plugins/agent_browser)
    assert "/api/plugins/agent_browser" in prefixes  # gated data routes
    assert registry.surfaces == []  # no dashboard lifecycle surface anymore


# ── manifest / version coherence + settings ──────────────────────────────────────


def test_manifest_and_pyproject_versions_match():
    import tomllib

    import yaml

    m = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())
    pp = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert m["version"] == pp["project"]["version"]  # the drift this test now guards
    assert m["id"] == "agent_browser" and m["enabled"] is False
    assert m["views"][0]["path"] == "/plugins/agent_browser/panel"


def test_settings_fields_are_valid_and_back_real_config():
    import yaml

    m = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())
    by_key = {f["key"]: f for f in m["settings"]}
    assert by_key["headed"]["type"] == "bool" and by_key["timeout_s"]["type"] == "number"
    # the switchover dropped these knobs entirely:
    assert "panel_mode" not in by_key and "dashboard_port" not in by_key
    assert "panel_mode" not in m["config"] and "manage_dashboard" not in m["config"]
    # every settings key has a declared default in config:
    assert set(by_key) <= set(m["config"])


# ── the panel routes (page / ticket / WS gating / nav) ───────────────────────────


def _app(cfg=None):
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(bp.build_panel_router(cfg or {}), prefix="/plugins/agent_browser")
    app.include_router(bp.build_panel_data_router(cfg or {}), prefix="/api/plugins/agent_browser")
    return app


def test_panel_page_wires_canvas_stream_and_input():
    from fastapi.testclient import TestClient

    html = TestClient(_app({})).get("/plugins/agent_browser/panel").text
    assert "/_ds/plugin-kit.css" in html  # DS kit
    assert 'location.pathname.split("/plugins/")[0]' in html  # slug-aware base
    assert 'id="cv"' in html and "createImageBitmap" in html  # the canvas + frame painting
    assert "/api/plugins/agent_browser/stream-ticket" in html  # mint a ticket (gated)
    assert "/api/plugins/agent_browser/stream" in html  # the WS stream
    assert 'u.protocol==="https:" ? "wss:" : "ws:"' in html  # http→ws upgrade
    assert 'send({t:"mouse"' in html and 'send({t:"key"' in html  # input forwarding
    assert "ResizeObserver" in html and 'send({t:"resize"' in html  # responsive viewport tracking
    assert "/api/plugins/agent_browser/nav" in html and "kit.apiFetch" in html  # nav via gated route
    assert "startBrowser" in html and 'const HOME="";' in html  # empty-state Start; blank home default
    # the removed dashboard-embed / screenshot modes leave no trace:
    assert "/api/plugins/agent_browser/shot" not in html
    assert 'id="f"' not in html and "Open the console locally" not in html


def test_panel_home_url_is_injected_safely():
    from fastapi.testclient import TestClient

    # a configured homepage lands as a JS string literal the Start button + auto-open use
    html = TestClient(_app({"home_url": "https://example.com"})).get("/plugins/agent_browser/panel").text
    assert 'const HOME="https://example.com";' in html
    assert "__HOME_URL__" not in html  # placeholder fully interpolated
    # a </script>-injection attempt is escaped: the quote is JSON-escaped and the `<`
    # becomes <, so it neither breaks the JS string nor closes the inline script.
    evil = TestClient(_app({"home_url": '"</script>'})).get("/plugins/agent_browser/panel").text
    assert 'const HOME="\\"\\u003c/script>";' in evil


def test_stream_ticket_route_mints_a_ticket():
    from fastapi.testclient import TestClient

    body = TestClient(_app()).post("/api/plugins/agent_browser/stream-ticket").json()
    assert isinstance(body.get("ticket"), str) and len(body["ticket"]) > 10


def test_stream_ws_rejects_a_bad_ticket():
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    c = TestClient(_app())
    # no valid ticket → handler closes (1008) before accept → connect raises.
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/api/plugins/agent_browser/stream?ticket=nope"):
            pass


def test_stream_ws_accepts_valid_ticket_then_reports_no_page(monkeypatch):
    from fastapi.testclient import TestClient

    # resolve returns no page → the handler accepts, sends an error frame, and closes
    # (exercises the ticket gate + accept path without a real browser/CDP).
    monkeypatch.setattr(bp.browser_stream, "resolve_page_target",
                        lambda binary, timeout: (None, "no page open"))
    c = TestClient(_app())
    ticket = c.post("/api/plugins/agent_browser/stream-ticket").json()["ticket"]
    with c.websocket_connect(f"/api/plugins/agent_browser/stream?ticket={ticket}") as ws:
        assert ws.receive_json() == {"t": "error", "msg": "no page open"}


def test_stream_ticket_is_single_use():
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    c = TestClient(_app())
    ticket = c.post("/api/plugins/agent_browser/stream-ticket").json()["ticket"]
    assert bp.browser_stream.consume_ticket(ticket) is True   # burn it directly
    with pytest.raises(WebSocketDisconnect):                  # replay is rejected
        with c.websocket_connect(f"/api/plugins/agent_browser/stream?ticket={ticket}"):
            pass


def test_nav_route_validates(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(bp.subprocess, "run", fake_run(record=[]))
    c = TestClient(_app())
    assert c.post("/api/plugins/agent_browser/nav", json={"action": "bogus"}).json()["ok"] is False
    assert c.post("/api/plugins/agent_browser/nav", json={"action": "open"}).json()["error"] == "url required"
    assert c.post("/api/plugins/agent_browser/nav", json={"action": "reload"}).json()["ok"] is True


def test_nav_open_applies_launch_flags(monkeypatch):
    from fastapi.testclient import TestClient

    rec = []
    monkeypatch.setattr(bp.subprocess, "run", fake_run(record=rec))
    # a session started from the panel gets the same headed/stealth setup as the agent's
    c = TestClient(_app({"headed": True, "stealth": True}))
    c.post("/api/plugins/agent_browser/nav", json={"action": "open", "url": "https://x.com"})
    argv = rec[-1]
    assert argv[-2:] == ["open", "https://x.com"]
    assert "--headed" in argv and "--args" in argv  # launch flags applied on open
    # back/forward/reload don't relaunch, so they carry no flags
    c.post("/api/plugins/agent_browser/nav", json={"action": "reload"})
    assert rec[-1] == ["agent-browser", "reload"]


