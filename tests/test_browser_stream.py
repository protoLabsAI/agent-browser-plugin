"""Tests for the interactive-panel CDP bridge — the pure, host-free brains of
``browser_stream``: CDP endpoint parsing, page-target selection, and the
input-message → CDP-command translation. These lock in the behavior validated
end-to-end against a live browser (mouse + keyboard land; screencast frames flow).
No websockets/binary/browser needed — the pure functions have no IO."""

from __future__ import annotations

import agent_browser.browser_stream as bs


# ── CDP endpoint parsing ─────────────────────────────────────────────────────────


def test_http_base_from_ws_url():
    assert bs._http_base_from_ws(
        "ws://127.0.0.1:52886/devtools/browser/4580bed9") == "http://127.0.0.1:52886"


# ── page-target selection ────────────────────────────────────────────────────────


def _t(type_, url, ws="ws://x"):
    return {"type": type_, "url": url, "webSocketDebuggerUrl": ws}


def test_pick_page_prefers_current_url():
    targets = [
        _t("page", "https://a.com", "ws://a"),
        _t("page", "https://b.com", "ws://b"),
    ]
    assert bs.pick_page_target(targets, "https://b.com") == "ws://b"


def test_pick_page_falls_back_to_first_real_page():
    targets = [
        _t("page", "chrome://newtab/", "ws://newtab"),
        _t("page", "https://real.com", "ws://real"),
    ]
    # no current-url match → skip chrome:// surfaces, take the first real page.
    assert bs.pick_page_target(targets, "https://gone.com") == "ws://real"


def test_pick_page_skips_non_page_and_missing_ws():
    targets = [
        {"type": "service_worker", "url": "x", "webSocketDebuggerUrl": "ws://sw"},
        {"type": "page", "url": "https://c.com"},  # no ws url → not streamable
        _t("page", "https://d.com", "ws://d"),
    ]
    assert bs.pick_page_target(targets, "") == "ws://d"


def test_pick_page_none_when_no_pages():
    assert bs.pick_page_target([{"type": "iframe", "url": "x", "webSocketDebuggerUrl": "ws://i"}], "") is None
    assert bs.pick_page_target([], "") is None


# ── input translation: mouse ─────────────────────────────────────────────────────


def test_mouse_down_maps_to_pressed_with_button_and_count():
    method, p = bs.input_to_cdp(
        {"t": "mouse", "action": "down", "x": 10, "y": 20, "button": "left", "clickCount": 2, "buttons": 1})
    assert method == "Input.dispatchMouseEvent"
    assert p["type"] == "mousePressed" and p["x"] == 10.0 and p["y"] == 20.0
    assert p["button"] == "left" and p["clickCount"] == 2 and p["buttons"] == 1


def test_mouse_move_has_no_button_or_clickcount():
    _, p = bs.input_to_cdp({"t": "mouse", "action": "move", "x": 5, "y": 6})
    assert p["type"] == "mouseMoved"
    assert "button" not in p and "clickCount" not in p


def test_mouse_up_maps_to_released():
    _, p = bs.input_to_cdp({"t": "mouse", "action": "up", "x": 1, "y": 2, "button": "left"})
    assert p["type"] == "mouseReleased"


def test_wheel_maps_to_mousewheel_deltas():
    method, p = bs.input_to_cdp({"t": "wheel", "x": 3, "y": 4, "dx": 0, "dy": 120})
    assert method == "Input.dispatchMouseEvent" and p["type"] == "mouseWheel"
    assert p["deltaX"] == 0.0 and p["deltaY"] == 120.0


# ── input translation: keyboard ──────────────────────────────────────────────────


def test_key_down_with_text_carries_text_and_vkey():
    method, p = bs.input_to_cdp(
        {"t": "key", "action": "down", "key": "a", "code": "KeyA", "text": "a", "keyCode": 65})
    assert method == "Input.dispatchKeyEvent" and p["type"] == "keyDown"
    assert p["text"] == "a" and p["key"] == "a" and p["code"] == "KeyA"
    assert p["windowsVirtualKeyCode"] == 65 and p["nativeVirtualKeyCode"] == 65


def test_key_up_omits_text():
    _, p = bs.input_to_cdp({"t": "key", "action": "up", "key": "a", "code": "KeyA", "text": "a"})
    assert p["type"] == "keyUp" and "text" not in p


def test_modifiers_bitmask():
    _, p = bs.input_to_cdp({"t": "mouse", "action": "move", "x": 0, "y": 0, "ctrl": True, "shift": True})
    assert p["modifiers"] == (2 | 8)  # Ctrl=2, Shift=8


# ── input translation: rejects non-drivable messages ─────────────────────────────


def test_unknown_messages_return_none():
    assert bs.input_to_cdp({"t": "bogus"}) is None
    assert bs.input_to_cdp({"t": "mouse", "action": "wat"}) is None
    assert bs.input_to_cdp({"t": "key", "action": "wat"}) is None


# ── resolve_page_target: IO paths (subprocess + json/list mocked) ─────────────────


def test_resolve_returns_note_when_no_session(monkeypatch):
    def no_url(args, **kw):
        import types
        return types.SimpleNamespace(returncode=1, stdout="", stderr="no session")
    monkeypatch.setattr(bs.subprocess, "run", no_url)
    ws, note = bs.resolve_page_target("ab")
    assert ws is None and "no session" in note


def test_resolve_finds_active_page(monkeypatch):
    import io
    import types

    def fake_run(args, **kw):
        if args[1:] == ["get", "cdp-url"]:
            return types.SimpleNamespace(returncode=0, stdout="ws://127.0.0.1:9/devtools/browser/x\n", stderr="")
        if args[1:] == ["get", "url"]:
            return types.SimpleNamespace(returncode=0, stdout="https://ex.com\n", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bs.subprocess, "run", fake_run)
    listing = b'[{"type":"page","url":"https://ex.com","webSocketDebuggerUrl":"ws://127.0.0.1:9/devtools/page/P"}]'
    monkeypatch.setattr(bs.urllib.request, "urlopen",
                        lambda url, timeout=0: io.BytesIO(listing))
    ws, note = bs.resolve_page_target("ab")
    assert ws == "ws://127.0.0.1:9/devtools/page/P" and note == ""


# ── WS auth tickets: single-use + expiry ─────────────────────────────────────────


def test_ticket_mint_then_consume_is_single_use():
    t = bs.mint_ticket()
    assert bs.consume_ticket(t) is True    # first use validates
    assert bs.consume_ticket(t) is False   # replay is burned


def test_consume_rejects_unknown_and_empty():
    assert bs.consume_ticket("never-minted") is False
    assert bs.consume_ticket("") is False


def test_ticket_expires_after_ttl(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(bs.time, "monotonic", lambda: clock["t"])
    t = bs.mint_ticket()
    clock["t"] += bs._TICKET_TTL + 1     # advance past the TTL
    assert bs.consume_ticket(t) is False


# ── viewport_metrics: clamp panel size → viewport + frame caps ──────────────────


def test_viewport_metrics_normal_hidpi():
    cw, ch, scale, mw, mh = bs.viewport_metrics(800, 1000, 2)
    assert (cw, ch, scale) == (800, 1000, 2.0)
    assert (mw, mh) == (1600, 2000)  # frame = css × dpr


def test_viewport_metrics_clamps_giant_dock():
    cw, ch, scale, mw, mh = bs.viewport_metrics(5000, 5000, 3)
    assert (cw, ch, scale) == (2048, 2048, 2.0)  # css ≤2048, dpr ≤2
    assert (mw, mh) == (2560, 2560)  # frame long side capped at 2560


def test_viewport_metrics_floors_degenerate():
    assert bs.viewport_metrics(0, 0, 0) == (1, 1, 1.0, 1, 1)
    # sub-1 dpr floors to 1.0 (never upscale-blur by pretending lo-dpi)
    assert bs.viewport_metrics(640, 480, 0.5)[2] == 1.0
