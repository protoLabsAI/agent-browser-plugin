"""Tests for the agent_browser plugin — the tool subprocess wrappers (arg-building +
graceful error degradation), the panel routers (page / four-rules / gating / proxy),
register() wiring, the dashboard lifecycle, and manifest/version coherence. Host-free:
subprocess.run is mocked, so no agent-browser binary and no real browser are needed."""

from __future__ import annotations

from pathlib import Path


import agent_browser.browser_panel as bp
import agent_browser.lifecycle as lc
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


async def test_dashboard_tool_validates_action_and_passes_port(monkeypatch):
    rec = []
    monkeypatch.setattr(tools.subprocess, "run", fake_run(record=rec))
    t = _toolmap({"binary": "ab", "dashboard_port": 9000})
    assert "action must be" in await t["browser_dashboard"].ainvoke({"action": "boom"})
    await t["browser_dashboard"].ainvoke({"action": "start"})
    assert rec[-1] == ["ab", "dashboard", "start", "--port", "9000"]


def test_all_17_tools_present():
    names = set(_toolmap())
    assert len(names) == 17
    assert {
        "browser_open",
        "browser_snapshot",
        "browser_click",
        "browser_fill",
        "browser_screenshot",
        "browser_eval",
        "browser_close",
        "browser_dashboard",
    } <= names


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


def test_register_wires_tools_panel_data_and_surface(registry):
    import agent_browser as pkg

    pkg.register(registry)
    assert len(registry.tools) == 17
    prefixes = [p for p, _ in registry.routers]
    assert None in prefixes  # the panel PAGE (host default prefix /plugins/agent_browser)
    assert "/api/plugins/agent_browser" in prefixes  # gated data routes
    assert registry.surfaces and registry.surfaces[0][0] == "agent-browser-dashboard"


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
    assert by_key["panel_mode"]["type"] == "select"
    assert set(by_key["panel_mode"]["options"]) == {"full", "minimal"}
    assert by_key["headed"]["type"] == "bool" and by_key["dashboard_port"]["type"] == "number"
    # every settings key has a declared default in config:
    assert set(by_key) <= set(m["config"])


# ── the dashboard lifecycle ──────────────────────────────────────────────────────


async def test_lifecycle_starts_on_boot_and_stops_on_shutdown(monkeypatch):
    rec = []
    monkeypatch.setattr(lc.subprocess, "run", fake_run(record=rec))
    start, stop = lc.make_dashboard_surface({"binary": "ab", "dashboard_port": 9999})
    await start()
    await stop()
    assert ["ab", "dashboard", "start", "--port", "9999"] in rec
    assert ["ab", "dashboard", "stop"] in rec


async def test_lifecycle_leaves_shared_dashboard_untouched(monkeypatch):
    rec = []
    monkeypatch.setattr(lc.subprocess, "run", fake_run(record=rec))
    start, stop = lc.make_dashboard_surface({"manage_dashboard": False})
    await start()
    await stop()
    assert rec == []  # manage_dashboard:false → never touches the daemon


# ── the panel routers (page / four-rules / gating / proxy) ───────────────────────


def _app(cfg=None):
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(bp.build_panel_router(cfg or {}), prefix="/plugins/agent_browser")
    app.include_router(bp.build_panel_data_router(cfg or {}), prefix="/api/plugins/agent_browser")
    return app


def test_panel_page_full_mode_is_four_rules_compliant():
    from fastapi.testclient import TestClient

    c = TestClient(_app({"panel_mode": "full"}))
    r = c.get("/plugins/agent_browser/panel")
    assert r.status_code == 200
    html = r.text
    assert "/_ds/plugin-kit.css" in html  # DS kit
    assert 'location.pathname.split("/plugins/")[0]' in html  # slug-aware base
    assert "/plugins/agent_browser/panel/dash/" in html  # same-origin proxied dashboard (embed)
    # never a hardcoded http origin (issue #6) — the iframe src is BASE-derived same-origin;
    # the "open directly" link is built from location.hostname, not a literal localhost.
    assert "http://localhost" not in html and "http://127.0.0.1" not in html
    assert "__DASH_PORT__" not in html  # the port placeholder is interpolated at serve time
    assert "/api/plugins/agent_browser/dashboard" in html  # the start/stop control


def test_panel_page_minimal_mode_uses_gated_data_routes():
    from fastapi.testclient import TestClient

    html = TestClient(_app({"panel_mode": "minimal", "dashboard_port": 4933})).get(
        "/plugins/agent_browser/panel"
    ).text
    assert "/api/plugins/agent_browser/shot" in html  # gated screenshot
    assert "/api/plugins/agent_browser/nav" in html  # gated nav
    assert "kit.apiFetch" in html  # authed fetch via the kit
    # the dashboard control (start it from the UI) + the port placeholder is interpolated
    assert "/api/plugins/agent_browser/dashboard" in html and "Start dashboard" in html
    assert "__DASH_PORT__" not in html and "4933" in html


def test_default_panel_mode_is_minimal():
    import yaml

    m = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())
    assert m["config"]["panel_mode"] == "minimal"  # the reliable mode is the default now
    by_key = {f["key"]: f for f in m["settings"]}
    assert by_key["panel_mode"]["options"][0] == "minimal"  # recommended first


def test_dashboard_control_endpoint(monkeypatch):
    from fastapi.testclient import TestClient

    c = TestClient(_app({"dashboard_port": 4934}))  # nothing listens → status "stopped"
    st = c.get("/api/plugins/agent_browser/dashboard").json()
    assert st["running"] is False and st["port"] == 4934
    # start/stop run the CLI (mocked); bad action is rejected.
    rec = []
    monkeypatch.setattr(bp.subprocess, "run", fake_run(record=rec))
    assert c.post("/api/plugins/agent_browser/dashboard", json={"action": "x"}).json()["ok"] is False
    assert c.post("/api/plugins/agent_browser/dashboard", json={"action": "start"}).json()["ok"] is True
    assert ["agent-browser", "dashboard", "start", "--port", "4934"] in rec
    assert c.post("/api/plugins/agent_browser/dashboard", json={"action": "stop"}).json()["ok"] is True
    assert ["agent-browser", "dashboard", "stop"] in rec


def test_shot_route_503s_without_a_frame(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(bp.subprocess, "run", fake_run(rc=127, stderr="not found"))
    monkeypatch.setattr(bp.os.path, "exists", lambda p: False)  # no cached frame
    monkeypatch.setattr(bp, "_shot_ts", 0.0)
    r = TestClient(_app()).get("/api/plugins/agent_browser/shot")
    assert r.status_code == 503


def test_nav_route_validates(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(bp.subprocess, "run", fake_run(record=[]))
    c = TestClient(_app())
    assert c.post("/api/plugins/agent_browser/nav", json={"action": "bogus"}).json()["ok"] is False
    assert c.post("/api/plugins/agent_browser/nav", json={"action": "open"}).json()["error"] == "url required"
    assert c.post("/api/plugins/agent_browser/nav", json={"action": "reload"}).json()["ok"] is True


def test_dash_proxy_502s_when_daemon_down():
    from fastapi.testclient import TestClient

    # nothing listens on the dashboard port in CI → the reverse proxy returns 502, not a crash.
    r = TestClient(_app({"dashboard_port": 4999})).get("/plugins/agent_browser/panel/dash/")
    assert r.status_code == 502
