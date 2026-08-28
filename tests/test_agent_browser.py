"""Tests for the agent_browser plugin — the tool subprocess wrappers (arg-building +
graceful error degradation), the interactive panel routes (page / ticket / WS gating /
nav), register() wiring, and manifest/version coherence. Host-free: subprocess.run is
mocked, so no agent-browser binary and no real browser are needed."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest

import agent_browser.browser_panel as bp
import agent_browser.tools as tools
from conftest import fake_run

ROOT = Path(__file__).resolve().parent.parent


def _toolmap(cfg=None):
    return {t.name: t for t in tools.get_browser_tools(cfg or {})}


# ── a subprocess.Popen stand-in for the tool wrappers ────────────────────────────
# _run() now streams the child's pipes through drain threads under a byte cap, so the
# tool tests mock Popen (not run): BytesIO pipes yield the canned bytes, wait()/kill()
# drive the timeout + reap paths.


class _FakeProc:
    """Minimal Popen: BytesIO pipes + wait/kill, enough for _run's drain loop."""

    def __init__(self, argv, out=b"", err=b"", rc=0, timeout=False):
        self._argv = list(argv)
        self.stdout = io.BytesIO(out)
        self.stderr = io.BytesIO(err)
        self._rc = rc
        self._timeout = timeout  # make wait(timeout=…) raise until killed
        self.returncode = None
        self.killed = False

    def wait(self, timeout=None):
        if self._timeout and timeout is not None and not self.killed:
            raise subprocess.TimeoutExpired(cmd=self._argv[0], timeout=timeout)
        if self.returncode is None:
            self.returncode = -9 if self.killed else self._rc
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def poll(self):
        return self.returncode


def fake_popen(out=b"", err=b"", rc=0, timeout=False, record=None, procs=None):
    """A subprocess.Popen stand-in: records argv, returns a _FakeProc whose pipes yield
    the canned bytes. Swallows the stdout=/stderr= PIPE kwargs the wrapper passes."""
    if isinstance(out, str):
        out = out.encode()
    if isinstance(err, str):
        err = err.encode()

    def _popen(argv, **kw):
        if record is not None:
            record.append(list(argv))
        p = _FakeProc(argv, out=out, err=err, rc=rc, timeout=timeout)
        if procs is not None:
            procs.append(p)
        return p

    return _popen


# ── the tools: arg-building ──────────────────────────────────────────────────────


async def test_open_passes_url_and_curated_launch_flags(monkeypatch):
    rec = []
    monkeypatch.setattr(tools.subprocess, "Popen", fake_popen(out="OPENED", record=rec))
    # headless so argv is clean (headed injects anti-throttle --args; covered in test_runtime)
    t = _toolmap({"binary": "ab", "allowed_domains": "x.com", "max_output": 500})
    out = await t["browser_open"].ainvoke({"url": "https://x.com"})
    assert "OPENED" in out
    assert rec[-1] == ["ab", "--allowed-domains", "x.com", "--max-output", "500", "open", "https://x.com"]


async def test_open_blank_url_omits_it(monkeypatch):
    rec = []
    monkeypatch.setattr(tools.subprocess, "Popen", fake_popen(record=rec))
    await _toolmap({"binary": "ab"})["browser_open"].ainvoke({})
    assert rec[-1] == ["ab", "open"]


async def test_action_tools_pass_refs(monkeypatch):
    rec = []
    monkeypatch.setattr(tools.subprocess, "Popen", fake_popen(record=rec))
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
    def boom(argv, **kw):
        raise FileNotFoundError()

    monkeypatch.setattr(tools.subprocess, "Popen", boom)
    out = await _toolmap({"binary": "nope"})["browser_snapshot"].ainvoke({})
    assert "not on PATH" in out and "npm i -g agent-browser" in out


async def test_timeout_returns_readable_error_and_reaps_child(monkeypatch):
    procs = []
    monkeypatch.setattr(tools.subprocess, "Popen", fake_popen(timeout=True, procs=procs))
    out = await _toolmap({"binary": "ab", "timeout_s": 1})["browser_snapshot"].ainvoke({})
    assert "timed out" in out
    assert procs[0].killed  # child terminated + reaped on timeout — never a zombie


async def test_nonzero_exit_surfaces_stderr(monkeypatch):
    monkeypatch.setattr(tools.subprocess, "Popen", fake_popen(rc=2, err="boom"))
    out = await _toolmap({"binary": "ab"})["browser_click"].ainvoke({"selector": "@e9"})
    assert out.startswith("Error:") and "boom" in out


# ── the tools: aggregate stdout+stderr byte cap (memory + context safety) ──────────


async def test_output_within_cap_is_unchanged(monkeypatch):
    # under the cap → behavior identical to before: raw stdout, stripped.
    monkeypatch.setattr(tools.subprocess, "Popen", fake_popen(out="hello world\n"))
    out = await _toolmap({"binary": "ab", "max_response_bytes": 100})["browser_get_text"].ainvoke({"selector": "body"})
    assert out == "hello world"


async def test_output_over_cap_is_truncated_with_diagnostic(monkeypatch):
    procs = []
    monkeypatch.setattr(tools.subprocess, "Popen", fake_popen(out=b"x" * 5000, procs=procs))
    t = _toolmap({"binary": "ab", "max_response_bytes": 100})
    out = await t["browser_get_text"].ainvoke({"selector": "body"})
    assert out == "Error: output exceeded 100 bytes (truncated)"
    assert procs[0].killed  # overflow kills the child cleanly


async def test_aggregate_stdout_plus_stderr_is_bounded(monkeypatch):
    # neither stream alone exceeds the cap, but together they do → still bounded.
    monkeypatch.setattr(tools.subprocess, "Popen", fake_popen(out=b"a" * 60, err=b"b" * 60))
    out = await _toolmap({"binary": "ab", "max_response_bytes": 100})["browser_snapshot"].ainvoke({})
    assert out == "Error: output exceeded 100 bytes (truncated)"


async def test_configured_cap_overrides_default(monkeypatch):
    # a small configured cap trips where the 200KB default would not.
    monkeypatch.setattr(tools.subprocess, "Popen", fake_popen(out=b"y" * 1000))
    out = await _toolmap({"binary": "ab", "max_response_bytes": 10})["browser_get_html"].ainvoke({})
    assert out == "Error: output exceeded 10 bytes (truncated)"


async def test_default_cap_is_200kb_when_unconfigured(monkeypatch):
    # no max_response_bytes key → 200000 default applies; 250KB overflows, and the
    # diagnostic names the default limit.
    monkeypatch.setattr(tools.subprocess, "Popen", fake_popen(out=b"z" * 250_000))
    out = await _toolmap({"binary": "ab"})["browser_get_text"].ainvoke({"selector": "body"})
    assert out == "Error: output exceeded 200000 bytes (truncated)"


async def test_output_at_the_cap_is_not_truncated(monkeypatch):
    # exactly the cap is allowed through (only strictly-larger output overflows).
    monkeypatch.setattr(tools.subprocess, "Popen", fake_popen(out=b"q" * 50))
    out = await _toolmap({"binary": "ab", "max_response_bytes": 50})["browser_get_text"].ainvoke({"selector": "body"})
    assert out == "q" * 50


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


def test_max_response_bytes_default_is_declared():
    import yaml

    m = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())
    assert m["config"]["max_response_bytes"] == 200000  # the wrapper's default cap
    by_key = {f["key"]: f for f in m["settings"]}
    assert by_key["max_response_bytes"]["type"] == "number"  # operator-editable knob


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
    assert "object-fit:contain" in html  # no distortion during resize
    assert "visibilitychange" in html and 'send({t:"refresh"' in html  # refresh when re-shown
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


# ── /panel/dash signed-cookie auth gate (mint token + proxy gate) ─────────────────


def test_dash_token_roundtrips_and_expires():
    # a fresh token verifies through its ~5-min window; one minted in the past is expired;
    # a flipped signature byte or a re-dated expiry can never forge the HMAC.
    tok = bp.mint_dash_token(now=1000, ttl=300)
    assert bp.verify_dash_token(tok, now=1000) is True
    assert bp.verify_dash_token(tok, now=1299) is True
    assert bp.verify_dash_token(tok, now=1301) is False           # past expiry → rejected
    exp, _, sig = tok.partition(".")
    bad_sig = sig[:-1] + ("1" if sig.endswith("0") else "0")
    assert bp.verify_dash_token(f"{exp}.{bad_sig}", now=1000) is False   # tampered signature
    assert bp.verify_dash_token(f"99999999999.{sig}", now=1000) is False  # re-dated expiry
    assert bp.verify_dash_token("", now=1000) is False
    assert bp.verify_dash_token("garbage-no-dot", now=1000) is False


def test_dash_session_mints_signed_token_without_api_cookie():
    from fastapi.testclient import TestClient

    r = TestClient(_app({"require_auth": True})).post("/api/plugins/agent_browser/dash-session")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert bp.verify_dash_token(body["dash"]) is True    # a real, verifiable signed token
    # NO Set-Cookie on the /api response: a Path=/plugins/agent_browser/ cookie would
    # not path-match this /api/... URL and browsers drop it (RFC 6265) — the ?dash=
    # exchange on the proxy itself sets the cookie instead.
    assert "set-cookie" not in r.headers


def test_dash_query_token_exchanges_for_path_matched_cookie():
    from fastapi.testclient import TestClient

    c = TestClient(_app({"require_auth": True}))
    r = c.get(f"/plugins/agent_browser/panel/dash?dash={bp.mint_dash_token()}")
    assert r.status_code == 200 and 'id="cv"' in r.text  # a valid minted token admits entry
    sc = r.headers["set-cookie"]
    low = sc.lower()
    assert sc.startswith("ab_session=")
    assert "httponly" in low                          # not readable from JS
    assert "samesite=strict" in low                   # no cross-site send
    assert "path=/plugins/agent_browser/" in low      # scoped to the plugin's page surface
    assert "max-age=300" in low                       # ~5-min TTL
    # the Path path-matches the URL that set it (RFC 6265 — else browsers drop the cookie)
    cookie_path = next(p.split("=", 1)[1] for p in sc.split(";")
                       if p.strip().lower().startswith("path="))
    assert "/plugins/agent_browser/panel/dash".startswith(cookie_path)
    # the cookie value is a real, verifiable signed token…
    token = sc.split("ab_session=", 1)[1].split(";", 1)[0]
    assert bp.verify_dash_token(token) is True
    # …and the retained cookie admits a plain reload with no query token
    assert c.get("/plugins/agent_browser/panel/dash").status_code == 200


def test_dash_cookie_path_follows_proxy_prefix():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # through the fleet proxy the page surface lives under /agents/<slug>/… — the cookie
    # Path must follow the actual request URL or the browser would never send it back.
    app = FastAPI()
    app.include_router(bp.build_panel_router({"require_auth": True}),
                       prefix="/agents/slug/plugins/agent_browser")
    r = TestClient(app).get(
        f"/agents/slug/plugins/agent_browser/panel/dash?dash={bp.mint_dash_token()}")
    assert "path=/agents/slug/plugins/agent_browser/" in r.headers["set-cookie"].lower()


def test_dash_cookie_is_secure_only_on_https():
    from fastapi.testclient import TestClient

    app = _app({"require_auth": True})
    url = "/plugins/agent_browser/panel/dash"
    http = TestClient(app).get(f"{url}?dash={bp.mint_dash_token()}")
    assert "secure" not in http.headers["set-cookie"].lower()          # plain HTTP → no Secure flag
    https = TestClient(app, base_url="https://testserver").get(f"{url}?dash={bp.mint_dash_token()}")
    assert "secure" in https.headers["set-cookie"].lower()             # HTTPS origin → Secure
    xfp = TestClient(app).get(f"{url}?dash={bp.mint_dash_token()}",
                              headers={"X-Forwarded-Proto": "https"})
    assert "secure" in xfp.headers["set-cookie"].lower()               # TLS-terminating proxy → Secure


def test_dash_proxy_rejects_without_cookie_when_gated():
    from fastapi.testclient import TestClient

    # token-gated deployment: the iframe proxy is unreachable without a valid cookie
    # or a valid minted ?dash= token.
    c = TestClient(_app({"require_auth": True}))
    assert c.get("/plugins/agent_browser/panel/dash").status_code == 401
    assert c.get("/plugins/agent_browser/panel/dash?dash=9999999999.forged").status_code == 401
    expired = bp.mint_dash_token(now=0)
    assert c.get(f"/plugins/agent_browser/panel/dash?dash={expired}").status_code == 401


def test_dash_proxy_serves_with_valid_cookie_when_gated():
    from fastapi.testclient import TestClient

    c = TestClient(_app({"require_auth": True}))
    token = bp.mint_dash_token()
    r = c.get("/plugins/agent_browser/panel/dash", headers={"Cookie": f"ab_session={token}"})
    assert r.status_code == 200 and 'id="cv"' in r.text                # the drivable dashboard served


def test_dash_proxy_rejects_expired_or_tampered_cookie_when_gated():
    from fastapi.testclient import TestClient

    c = TestClient(_app({"require_auth": True}))
    expired = bp.mint_dash_token(now=0)                                # exp far in the past
    assert c.get("/plugins/agent_browser/panel/dash",
                 headers={"Cookie": f"ab_session={expired}"}).status_code == 401
    assert c.get("/plugins/agent_browser/panel/dash",
                 headers={"Cookie": "ab_session=9999999999.deadbeef"}).status_code == 401   # bad signature


def test_dash_proxy_open_when_not_gated():
    from fastapi.testclient import TestClient

    # backward compatible: a deployment that doesn't require bearer auth serves the proxy
    # even with no cookie at all.
    r = TestClient(_app({})).get("/plugins/agent_browser/panel/dash")
    assert r.status_code == 200 and 'id="cv"' in r.text


def test_panel_page_mints_dash_session_before_stream():
    from fastapi.testclient import TestClient

    html = TestClient(_app({})).get("/plugins/agent_browser/panel").text
    # the panel mints a token via the (bearer-gated) dash-session route, then presents
    # it as ?dash= to the proxy — whose response sets the path-matched ab_session cookie…
    assert "/api/plugins/agent_browser/dash-session" in html and "ensureDashSession" in html
    assert "/plugins/agent_browser/panel/dash?dash=" in html
    # …and it does so from connect(), before the stream WS handshake.
    assert "await ensureDashSession();" in html
    assert html.index("await ensureDashSession();") < html.index("new WebSocket(")


