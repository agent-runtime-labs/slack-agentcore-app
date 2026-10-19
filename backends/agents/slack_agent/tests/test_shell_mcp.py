"""Tests for staging limits and per-request isolation (no real MCP required)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from shell_mcp import stage_text_documents, _safe_name, enabled


def test_stage_text_and_keep_other_blocks(tmp_path):
    doc = {"document": {"format": "txt", "name": "application log", "source": {"bytes": b"ERROR timeout\n"}}}
    image = {"image": {"format": "png", "source": {"bytes": b"png"}}}
    remaining, names = stage_text_documents([doc, image], tmp_path)
    assert remaining == [image]
    assert names == ["attachment-1.txt"]
    assert (tmp_path / names[0]).read_text() == "ERROR timeout\n"


def test_no_path_traversal(tmp_path):
    assert _safe_name("../../secret.log", 1) == "attachment-1.log"


def test_invalid_text_not_staged(tmp_path):
    doc = {"document": {"format": "txt", "name": "bad.txt", "source": {"bytes": b"\xff"}}}
    remaining, names = stage_text_documents([doc], tmp_path)
    assert remaining == [doc] and names == []


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SHELL_MCP_ENABLED", raising=False)
    assert not enabled()
