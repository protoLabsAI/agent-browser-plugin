"""Host-free test bootstrap. Register the plugin under a synthetic `agent_browser` package
so the modules' relative imports resolve with no protoAgent host — the host-only imports are
lazy (inside register()), so importing needs only the dev deps (fastapi + langchain-core).
The browser tools shell out to the `agent-browser` CLI, so the suite mocks subprocess.run —
no real binary, no real browser."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = "agent_browser"

if PKG not in sys.modules:
    _spec = importlib.util.spec_from_file_location(PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    assert _spec and _spec.loader
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[PKG] = _mod
    _spec.loader.exec_module(_mod)


class FakeRegistry:
    """Mirrors the registry surface register() touches."""

    def __init__(self, config=None):
        self.config = config or {}
        self.tools = []
        self.routers = []
        self.surfaces = []
        self.skill_dirs = []
        self.workflow_dirs = []

    def register_tool(self, t):
        self.tools.append(t)

    def register_router(self, router, prefix=None):
        self.routers.append((prefix, router))

    def register_surface(self, start, stop=None, name=None):
        self.surfaces.append((name, start, stop))

    def register_skill_dir(self, path):
        self.skill_dirs.append(path)

    def register_workflow_dir(self, path):
        self.workflow_dirs.append(path)


@pytest.fixture
def registry():
    return FakeRegistry()


def fake_run(rc=0, stdout="ok", stderr="", record=None):
    """A subprocess.run stand-in: records the argv and returns a canned CompletedProcess."""

    def _run(args, **kw):
        if record is not None:
            record.append(list(args))
        return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)

    return _run
