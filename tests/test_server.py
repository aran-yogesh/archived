"""Tests for the MCP tool layer (dedup loop + token-lean formats)."""

import pytest

from archived import server, store


@pytest.fixture(autouse=True)
def fresh_db():
    """Point the server at a fresh in-memory database for each test."""
    server._conn = store.connect(":memory:")
    yield
    server._conn.close()
    server._conn = None


def test_save_and_search_roundtrip():
    out = server.save_memory("churn-project uses XGBoost",
                             meta={"tags": ["churn-project"]})
    assert out == "Saved #1"
    hits = server.search_memory("xgboost")
    assert "#1 [fact] churn-project uses XGBoost" in hits


def test_dedup_loop_blocks_then_forces():
    server.save_memory("prefers pytest over unittest")
    out = server.save_memory("likes pytest for unit testing")
    assert out.startswith("NOT SAVED")
    assert "#1" in out  # shows the similar memory
    out = server.save_memory("likes pytest for unit testing",
                             meta={"force": True})
    assert out.startswith("Saved")


def test_dedup_loop_replace_supersedes():
    server.save_memory("uses unittest for tests")
    out = server.save_memory("switched to pytest for tests",
                             meta={"replace_id": 1})
    assert out == "Saved #2"
    assert "OUTDATED — superseded by #2" in server.get_memory(1)
    assert "#1" not in server.search_memory("tests unittest pytest")


def test_logs_skip_dedup():
    server.save_memory("worked on auth today", meta={"type": "log"})
    out = server.save_memory("worked on auth again", meta={"type": "log"})
    assert out.startswith("Saved")  # logs never hit the dedup loop


def test_search_is_headlines_only():
    server.save_memory("auth uses JWT", body="long decision details here")
    hits = server.search_memory("JWT")
    assert "long decision details" not in hits  # body only via get_memory
    assert "long decision details" in server.get_memory(1)


def test_get_missing_memory():
    assert server.get_memory(99) == "No memory #99."


def test_get_memory_marks_recalled():
    server.save_memory("token budget is 8k for hooks")
    server.get_memory(1)
    server.get_memory(1)
    assert store.get(server._conn, 1)["recall_count"] == 2


def test_recent_diary():
    server.save_memory("built the storage core", meta={"type": "log",
                                                       "project": "archived"})
    out = server.recent_memories()
    assert "built the storage core" in out
    assert "(archived)" in out
    assert server.recent_memories(project="nope") == "No session logs yet."
