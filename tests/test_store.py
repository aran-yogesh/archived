"""Tests for the archived storage core."""

import pytest

from archived import store


@pytest.fixture
def conn():
    """Fresh in-memory database for each test."""
    c = store.connect(":memory:")
    yield c
    c.close()


def test_save_and_get(conn):
    mid = store.save(conn, {"headline": "prefers pytest over unittest",
                            "tags": ["testing", "python"]})
    mem = store.get(conn, mid)
    assert mem["headline"] == "prefers pytest over unittest"
    assert mem["type"] == "fact"
    assert "testing" in mem["tags"]


def test_save_requires_headline(conn):
    with pytest.raises(ValueError):
        store.save(conn, {"headline": "  "})


def test_save_rejects_bad_type(conn):
    with pytest.raises(ValueError):
        store.save(conn, {"headline": "x", "type": "banana"})


def test_search_finds_by_keyword(conn):
    store.save(conn, {"headline": "churn-project uses XGBoost",
                      "tags": ["churn-project", "modeling"]})
    store.save(conn, {"headline": "prefers dark mode terminals"})
    hits = store.search(conn, "xgboost model")
    assert len(hits) == 1
    assert hits[0]["headline"] == "churn-project uses XGBoost"
    # token-lean: search returns headlines, never bodies
    assert "body" not in hits[0]


def test_search_headline_outranks_body(conn):
    store.save(conn, {"headline": "auth uses JWT",
                      "body": "decided after comparing with sessions"})
    store.save(conn, {"headline": "misc notes",
                      "body": "JWT JWT JWT mentioned in passing"})
    hits = store.search(conn, "JWT auth")
    assert hits[0]["headline"] == "auth uses JWT"


def test_search_respects_limit_and_project(conn):
    for i in range(8):
        store.save(conn, {"headline": f"redis note {i}", "project": "api"})
    store.save(conn, {"headline": "redis note other", "project": "web"})
    hits = store.search(conn, "redis", {"limit": 3, "project": "api"})
    assert len(hits) == 3
    assert all(h["project"] == "api" for h in hits)


def test_supersede_hides_old_memory(conn):
    old = store.save(conn, {"headline": "uses unittest"})
    store.save(conn, {"headline": "switched to pytest", "replace_id": old})
    hits = store.search(conn, "unittest pytest")
    ids = [h["id"] for h in hits]
    assert old not in ids
    assert store.get(conn, old)["superseded_by"] is not None


def test_find_similar_for_dedup(conn):
    store.save(conn, {"headline": "prefers pytest over unittest",
                      "tags": ["testing"]})
    candidates = store.find_similar(
        conn, {"headline": "likes pytest for testing", "tags": ["testing"]})
    assert candidates
    assert "pytest" in candidates[0]["headline"]


def test_logs_get_recency_boost(conn):
    store.save(conn, {"headline": "deploy scripts live in infra repo",
                      "type": "fact"})
    store.save(conn, {"headline": "worked on deploy scripts today",
                      "type": "log"})
    hits = store.search(conn, "deploy scripts")
    assert hits[0]["type"] == "log"  # fresh log outranks fact on equal match


def test_recent_returns_diary_newest_first(conn):
    store.save(conn, {"headline": "session one", "type": "log"})
    store.save(conn, {"headline": "session two", "type": "log"})
    store.save(conn, {"headline": "a fact", "type": "fact"})
    entries = store.recent(conn)
    assert [e["headline"] for e in entries][:2] == ["session two", "session one"]


def test_hot_slot_overwrites(conn):
    store.set_hot(conn, "churn", "left off: fixing imbalance")
    store.set_hot(conn, "churn", "left off: tuning SMOTE")
    assert store.get_hot(conn, "churn") == "left off: tuning SMOTE"
    assert store.get_hot(conn, "unknown") == ""
    # hot slots never leak into search results
    assert store.search(conn, "SMOTE tuning") == []


def test_fts_query_survives_punctuation(conn):
    store.save(conn, {"headline": "uses uv for python packaging"})
    hits = store.search(conn, 'what "packaging" tool (uv)?!')
    assert hits
