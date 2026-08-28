"""Guards the coder-grounding docs: PROTO.md is the canonical instructions file and must
name the gate command, test patterns, key invariants, and the file layout; CLAUDE.md and
AGENTS.md are thin pointers back to it. Host-free — pure file reads, like the manifest
coherence tests."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_proto_md_exists_at_repo_root():
    assert (ROOT / "PROTO.md").is_file()


def test_proto_md_names_the_gate_command():
    proto = (ROOT / "PROTO.md").read_text()
    assert "ruff check ." in proto
    assert "pytest" in proto


def test_proto_md_covers_test_patterns():
    proto = (ROOT / "PROTO.md").read_text()
    for token in ("fake_run", "FakeRegistry", "TestClient"):
        assert token in proto, f"PROTO.md should describe the {token} test pattern"


def test_proto_md_states_key_invariants():
    proto = (ROOT / "PROTO.md").read_text()
    # tools degrade to Error: strings, never raise
    assert "Error:" in proto and "raise" in proto
    # version must match in both manifests
    assert "protoagent.plugin.yaml" in proto and "pyproject.toml" in proto
    # host imports are lazy inside register()-time functions
    assert "lazy" in proto and "register()" in proto


def test_proto_md_lists_the_file_layout():
    proto = (ROOT / "PROTO.md").read_text()
    for fname in ("tools.py", "browser_panel.py", "browser_stream.py", "runtime.py", "__init__.py"):
        assert fname in proto, f"PROTO.md should list {fname}"


def test_proto_md_identifies_the_plugin_and_cli():
    proto = (ROOT / "PROTO.md").read_text()
    assert "agent_browser" in proto  # the plugin id
    assert "agent-browser" in proto  # the vercel-labs CLI it wraps


@pytest.mark.parametrize("pointer", ["CLAUDE.md", "AGENTS.md"])
def test_pointer_files_point_to_proto(pointer):
    path = ROOT / pointer
    assert path.is_file(), f"{pointer} should exist"
    assert "PROTO.md" in path.read_text(), f"{pointer} should point to PROTO.md"
