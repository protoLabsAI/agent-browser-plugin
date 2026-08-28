"""The browser tools — thin subprocess wrappers over the `agent-browser` CLI.

agent-browser (vercel-labs) is a native-Rust CLI + daemon: each invocation talks to
a persistent browser session, so these tools are stateless shells. The model's loop
is **open → snapshot → act on an `@eN` ref → verify**; `snapshot` returns the
accessibility tree with the refs the other tools consume.

Tools return the CLI's stdout (refs, extracted text, file paths) for the model to
read, and degrade to a readable ``Error: …`` string rather than raising — a failed
browser action should inform the loop, not crash it.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import threading

from langchain_core.tools import tool

from .runtime import launch_flags

log = logging.getLogger("protoagent.plugins.agent_browser")

# Default aggregate stdout+stderr byte cap when `max_response_bytes` is unset.
_DEFAULT_MAX_RESPONSE_BYTES = 200_000
_READ_CHUNK = 65_536


def get_browser_tools(cfg: dict | None):
    cfg = cfg or {}
    binary = str(cfg.get("binary") or "agent-browser")
    timeout = float(cfg.get("timeout_s", 60))
    # Plugin-owned cap on the total bytes a single invocation may buffer. Untrusted page
    # content (get text/html, eval) can emit unbounded output that would otherwise pile up
    # in memory and flood the model's context window, so we read the pipes incrementally
    # and stop the child the moment the aggregate crosses the cap.
    max_bytes = int(cfg.get("max_response_bytes", _DEFAULT_MAX_RESPONSE_BYTES) or _DEFAULT_MAX_RESPONSE_BYTES)

    def _run(*args: str) -> str:
        """Run `agent-browser <args>` and return stdout, or a readable error.

        Uses Popen with one drain thread per pipe (concurrent, so a full stderr can't
        deadlock stdout) enforcing an aggregate byte cap owned here. On overflow the child
        is killed and a bounded diagnostic is returned; on timeout the child is terminated.
        Either way the child is reaped — no zombies.
        """
        try:
            proc = subprocess.Popen([binary, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except FileNotFoundError:
            return (f"Error: {binary!r} not on PATH — install it: "
                    "`npm i -g agent-browser && agent-browser install`")

        out_buf, err_buf = bytearray(), bytearray()
        total = 0
        overflow = False
        lock = threading.Lock()

        def _drain(pipe, buf: bytearray) -> None:
            nonlocal total, overflow
            hit = False
            try:
                for block in iter(lambda: pipe.read(_READ_CHUNK), b""):
                    with lock:
                        if overflow:
                            break
                        total += len(block)
                        if total > max_bytes:  # aggregate crossed the cap → stop + kill
                            overflow = True
                            hit = True
                            break
                        buf.extend(block)
            except (OSError, ValueError):
                pass  # pipe closed under us (e.g. after kill) — nothing more to read
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass
            if hit:
                proc.kill()  # unblock the sibling reader and let wait() return

        drains = [threading.Thread(target=_drain, args=(proc.stdout, out_buf), daemon=True),
                  threading.Thread(target=_drain, args=(proc.stderr, err_buf), daemon=True)]
        for t in drains:
            t.start()

        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()  # terminate …
            try:
                proc.wait(timeout=5)  # … and reap, so we never leave a zombie
            except subprocess.TimeoutExpired:
                pass
        for t in drains:
            t.join()

        if timed_out:
            return f"Error: `agent-browser {' '.join(args)}` timed out after {timeout:g}s"
        if overflow:
            return f"Error: output exceeded {max_bytes} bytes (truncated)"
        out = out_buf.decode("utf-8", "replace").strip()
        if proc.returncode != 0:
            err = (err_buf.decode("utf-8", "replace") or out or "").strip()
            return f"Error: `agent-browser {' '.join(args)}` failed: {err[:500]}"
        return out or "(ok)"

    async def _ab(*args: str) -> str:
        return await asyncio.to_thread(_run, *args)

    # ── navigation ────────────────────────────────────────────────────────────
    @tool
    async def browser_open(url: str = "") -> str:
        """Launch the browser (or navigate the current session). Pass a `url` to go
        there, or leave blank to open about:blank. Start every browsing task here.
        The configured runtime options (headed/profile/device/allowed_domains/stealth/…)
        are applied here, where the session launches."""
        flags = launch_flags(cfg)
        return await _ab(*flags, "open", url) if url else await _ab(*flags, "open")

    @tool
    async def browser_back() -> str:
        """Navigate back in history."""
        return await _ab("back")

    @tool
    async def browser_forward() -> str:
        """Navigate forward in history."""
        return await _ab("forward")

    @tool
    async def browser_reload() -> str:
        """Reload the current page."""
        return await _ab("reload")

    # ── perception ────────────────────────────────────────────────────────────
    @tool
    async def browser_snapshot() -> str:
        """Get the page's accessibility tree with compact `@eN` element refs. Call
        this before clicking/filling — the refs (e.g. `@e2`) are what the action
        tools target. The canonical way to 'see' the page for the agent."""
        return await _ab("snapshot")

    @tool
    async def browser_get_text(selector: str = "body") -> str:
        """Get the visible text of an element (a `@eN` ref or a CSS selector;
        defaults to the whole `body`). Use to extract/read page content."""
        return await _ab("get", "text", selector)

    @tool
    async def browser_get_html(selector: str = "") -> str:
        """Get the HTML of an element (a `@eN` ref or CSS selector), or the page."""
        return await _ab("get", "html", selector) if selector else await _ab("get", "html")

    @tool
    async def browser_get_value(selector: str) -> str:
        """Get the current value of a form field (a `@eN` ref or CSS selector)."""
        return await _ab("get", "value", selector)

    # ── interaction ───────────────────────────────────────────────────────────
    @tool
    async def browser_click(selector: str) -> str:
        """Click an element by `@eN` ref (from `browser_snapshot`) or a CSS selector
        (e.g. `#submit`). Snapshot first to get the ref."""
        return await _ab("click", selector)

    @tool
    async def browser_fill(selector: str, text: str) -> str:
        """Clear a field and fill it with `text` (a `@eN` ref or CSS selector)."""
        return await _ab("fill", selector, text)

    @tool
    async def browser_type(selector: str, text: str) -> str:
        """Type `text` into an element without clearing it first (a ref or selector)."""
        return await _ab("type", selector, text)

    @tool
    async def browser_press(key: str) -> str:
        """Press a key or chord on the focused element (e.g. `Enter`, `Tab`,
        `Control+a`)."""
        return await _ab("press", key)

    @tool
    async def browser_hover(selector: str) -> str:
        """Hover the pointer over an element (a `@eN` ref or CSS selector)."""
        return await _ab("hover", selector)

    @tool
    async def browser_eval(expression: str) -> str:
        """Evaluate a JavaScript `expression` in the page and return the result.
        Use sparingly — prefer snapshot + the action tools."""
        return await _ab("eval", expression)

    # ── capture + session ─────────────────────────────────────────────────────
    @tool
    async def browser_screenshot(path: str = "page.png") -> str:
        """Save a screenshot of the current page to `path`. Returns the file path."""
        return await _ab("screenshot", path)

    @tool
    async def browser_close() -> str:
        """Close the browser session. Call when the task is done to free the daemon."""
        return await _ab("close")

    return [
        browser_open, browser_back, browser_forward, browser_reload,
        browser_snapshot, browser_get_text, browser_get_html, browser_get_value,
        browser_click, browser_fill, browser_type, browser_press, browser_hover,
        browser_eval, browser_screenshot, browser_close,
    ]
