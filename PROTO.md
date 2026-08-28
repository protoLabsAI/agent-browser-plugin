# PROTO.md — coder grounding for agent-browser-plugin

Canonical agent-instructions file for this repo. Read it before you touch code so you
ground on the real conventions instead of guessing. `CLAUDE.md` and `AGENTS.md` are thin
pointers back here.

## What this repo is

A **standalone protoAgent plugin** (`id: agent_browser`) that gives a protoAgent agent a
real browser. It is a set of **thin subprocess wrappers over the
[`agent-browser`](https://github.com/vercel-labs/agent-browser) CLI** (vercel-labs) — a
native-Rust CLI/daemon that drives Chrome over CDP with accessibility-tree snapshots and
compact `@eN` element refs. The plugin does **not** reimplement browser automation or a
renderer; it shells out to the CLI and bridges its CDP screencast to an interactive
console panel.

Ships **disabled** (`enabled: false`); an agent turns it on with
`plugins: { enabled: [agent_browser] }` and puts the `agent-browser` binary on PATH
(`npm i -g agent-browser && agent-browser install`).

## Stack

- **Python 3.12** (the repo `.venv`). Minimum is 3.11 — the manifest/version test uses
  `tomllib`, which does not exist on the system `python3` (3.9). Use the repo `.venv`,
  never bare `python3`.
- **FastAPI routers** for the console panel (page route + gated data routes + a WebSocket
  stream). Registered via `registry.register_router(...)`.
- **langchain-core tools** — the browser tools are `@tool`-decorated coroutines.
- **No build step.** Pure Python; the only external runtime dependency is the
  `agent-browser` CLI on PATH (so `pyproject.toml` declares no install deps). Host runtime
  deps (langchain-core, fastapi) come from the protoAgent host.

## Gate — run before every push

```
.venv/bin/ruff check . && .venv/bin/python -m pytest -q
```

Absolute form (what CI / the loop runs):

```
/Users/kj/dev/agent-browser-plugin/.venv/bin/ruff check . && \
/Users/kj/dev/agent-browser-plugin/.venv/bin/python -m pytest -q
```

The suite is **host-free**: it mocks the `agent-browser` CLI and the protoAgent host, so it
needs no real binary and no real browser — just the dev deps (`fastapi` + `langchain-core`
+ `pyyaml`, see `requirements-dev.txt`). Ruff config lives in `pyproject.toml`
(line-length 120, `E/F/W`).

## File layout

| File | What it holds |
| --- | --- |
| `__init__.py` | `register(registry)` — the plugin entry point. Wires the tools, mounts the two panel routers, and lets `skills/` + `workflows/` auto-discover. Host imports are lazy inside here. |
| `tools.py` | `get_browser_tools(cfg)` — the 16 `@tool` subprocess wrappers over the CLI (open, snapshot, click, fill, type, screenshot, …). The `_run` helper is where errors degrade to `Error:` strings. |
| `runtime.py` | `launch_flags(cfg)` — turns curated config knobs (headed / profile / device / allowed_domains / stealth / …) into `agent-browser` global flags. Dependency-free (no langchain) because the panel imports it too. |
| `browser_panel.py` | FastAPI routers for the console view: `build_panel_router` (public page) + `build_panel_data_router` (gated nav / stream-ticket / WS). |
| `browser_stream.py` | The CDP screencast bridge — resolves the page target, mints/consumes single-use WS tickets, pumps frames + operator input over the WebSocket. |
| `protoagent.plugin.yaml` | Plugin manifest — id, version, config defaults, Settings UI fields, console view declaration. |
| `pyproject.toml` | Package metadata + ruff/pytest config. |
| `tests/` | Host-free suite (see below). |
| `skills/`, `workflows/` | Auto-discovered discovery skill + browser workflows (ADR 0027 — no register call). |

## Test patterns

Tests live in `tests/` and are host-free. Match these when adding coverage:

- **Mock the CLI** — monkeypatch `subprocess.run` with `conftest.fake_run(...)`. It records
  the argv it was called with and returns a canned `CompletedProcess`, so you can assert
  the exact `agent-browser` command a tool built (e.g.
  `assert rec[-1] == ["ab", "click", "@e2"]`) and simulate non-zero exits / timeouts /
  missing binary.
- **`register()` wiring** — the `registry` fixture provides a `FakeRegistry` that mirrors
  the registry surface `register()` touches (`register_tool`, `register_router`,
  `register_surface`, `register_skill_dir`, `register_workflow_dir`). Assert what got
  registered.
- **Panel routes** — build a `FastAPI` app from `build_panel_router` +
  `build_panel_data_router` and exercise it with a FastAPI/Starlette `TestClient`
  (page HTML, `stream-ticket`, WS ticket gating, the `nav` route). WebSocket handshakes use
  `TestClient.websocket_connect`.
- **Manifest coherence** — read `protoagent.plugin.yaml` + `pyproject.toml` from disk and
  assert the version match, settings-field validity, and view path.

`conftest.py` registers the package under a synthetic `agent_browser` name so the modules'
relative imports resolve with no host present.

## Key invariants

- **Tools degrade, never raise.** Every browser tool returns the CLI's stdout on success or
  a readable `Error: …` string on failure (missing binary, non-zero exit, timeout). A
  failed browser action must inform the model's loop, not crash the agent. Do not let an
  exception escape a `@tool`.
- **Version is declared in two places and must match** — `protoagent.plugin.yaml`
  (`version:`) **and** `pyproject.toml` (`[project] version`). Bump both together when you
  cut a release; `test_manifest_and_pyproject_versions_match` guards the drift.
- **Host imports are lazy.** Anything from the protoAgent host (and langchain, for the
  panel's sake) is imported *inside* the `register()`-time functions (`register()`,
  `get_browser_tools`, the router builders) — never at module top level. This keeps the
  package importable host-free for the test suite. `runtime.py` in particular stays
  dependency-free so the panel can import it without dragging in langchain.
- **Panel auth split.** The page route mounts on the public `/plugins/agent_browser` (an
  iframe page-load can't carry a bearer); the data routes mount under
  `/api/plugins/agent_browser` so they inherit the operator bearer gate. The WebSocket
  gates itself with a single-use ticket (host auth middleware doesn't cover WS handshakes).
- **Composition over construction.** When something is browser behavior, prefer passing it
  through to the `agent-browser` CLI over reimplementing it here.

## Describing a change

There is no per-PR changelog obligation and nothing in the gate checks for one. **Describe
what changed in the PR title and the commit message** — that is the record reviewers read.
A human-facing `CHANGELOG.md`, keyed by released version, is maintained when a release is
cut (bump the version in both files + add the note); it is release notes, not a per-PR gate.
