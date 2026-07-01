"""Tests for the shared launch-flag builder — the curated knobs and the anti-detection
(stealth) layer. Pure + host-free (no subprocess, no binary)."""

from __future__ import annotations

import agent_browser.runtime as rt


def test_default_is_empty():
    assert rt.launch_flags({}) == []
    assert rt.launch_flags(None) == []


def test_curated_flags_stable_order():
    f = rt.launch_flags({"headed": True, "profile": "P", "device": "iPhone 16 Pro",
                         "allowed_domains": "x.com", "confirm_actions": "nav", "max_output": 500})
    assert f == ["--headed", "--profile", "P", "--device", "iPhone 16 Pro",
                 "--allowed-domains", "x.com", "--confirm-actions", "nav", "--max-output", "500"]


def test_stealth_headless_adds_automation_arg_and_real_ua():
    f = rt.launch_flags({"stealth": True})  # headless by default
    assert f[f.index("--user-agent") + 1].startswith("Mozilla/5.0")
    assert "HeadlessChrome" not in f[f.index("--user-agent") + 1]
    assert "--disable-blink-features=AutomationControlled" in f[f.index("--args") + 1]


def test_stealth_headed_skips_ua_but_keeps_automation_arg():
    f = rt.launch_flags({"stealth": True, "headed": True})
    assert "--user-agent" not in f  # a headed browser already reports a real UA
    assert "--disable-blink-features=AutomationControlled" in f[f.index("--args") + 1]


def test_explicit_ua_wins_and_browser_args_merge():
    f = rt.launch_flags({"stealth": True, "user_agent": "UA/1", "browser_args": "--foo, --bar"})
    assert f[f.index("--user-agent") + 1] == "UA/1"  # explicit override beats the stealth default
    args = f[f.index("--args") + 1].split(",")
    assert "--foo" in args and "--bar" in args
    assert "--disable-blink-features=AutomationControlled" in args  # merged, not duplicated
    assert args.count("--disable-blink-features=AutomationControlled") == 1


def test_browser_args_without_stealth_passes_through():
    f = rt.launch_flags({"browser_args": "--mute-audio"})
    assert f == ["--args", "--mute-audio"]
    assert "--user-agent" not in f  # no stealth → no UA injection
