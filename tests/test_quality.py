"""Tests for the dedup and doctor maintenance passes."""

from array import array

import pytest

from archived import embed, quality, store


@pytest.fixture
def conn():
    """Fresh in-memory database for each test."""
    c = store.connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def grouped_embeddings(monkeypatch):
    """Deterministic embeddings: facts sharing a keyword get one vector.

    Avoids depending on the real model. embed_text encodes a group id per
    fact; the patched cosine treats identical vectors as duplicates, so
    clustering is exact and fast.
    """
    def fake_embed(text):
        gid = 1.0 if "pytest" in text else 2.0
        return array("f", [gid]).tobytes()

    monkeypatch.setattr(embed, "embed_text", fake_embed)
    monkeypatch.setattr(embed, "cosine",
                        lambda a, b: 1.0 if list(a) == list(b) else 0.0)


def test_duplicate_clusters_groups_near_identical(conn, grouped_embeddings):
    a = store.save(conn, {"headline": "prefers pytest for tests"})
    b = store.save(conn, {"headline": "likes pytest a lot"})
    store.save(conn, {"headline": "deploys with terraform"})
    clusters = quality.duplicate_clusters(conn)
    assert clusters == [[a, b]]


def test_dedup_preview_changes_nothing(conn, grouped_embeddings):
    a = store.save(conn, {"headline": "prefers pytest for tests"})
    store.save(conn, {"headline": "likes pytest a lot"})
    quality.dedup(conn, apply=False)
    assert store.get(conn, a)["superseded_by"] is None


def test_dedup_apply_supersedes_older(conn, grouped_embeddings):
    a = store.save(conn, {"headline": "prefers pytest for tests"})
    b = store.save(conn, {"headline": "likes pytest a lot"})
    quality.dedup(conn, apply=True)
    assert store.get(conn, a)["superseded_by"] == b  # newest kept live
    assert store.get(conn, b)["superseded_by"] is None


def test_doctor_reports_counts_and_issues(conn, grouped_embeddings):
    store.save(conn, {"headline": "prefers pytest for tests"})
    store.save(conn, {"headline": "likes pytest a lot"})  # a duplicate
    store.save(conn, {"headline": "a fact with no body"})
    store.save(conn, {"headline": "a log entry", "type": "log"})
    rep = quality.doctor(conn)
    assert rep["by_type"]["fact"] == 3
    assert rep["by_type"]["log"] == 1
    assert rep["duplicate_clusters"] == 1
    assert rep["empty_body_facts"] == 3
    assert rep["embedded"] == 4


def test_normalize_text_collapses_whitespace():
    assert store.normalize_text("  a\n  b\t c  ") == "a b c"
    assert store.normalize_text(None) == ""


def test_mined_source_tracking_is_idempotent(conn):
    assert not store.was_mined(conn, "/x.jsonl", 100.0)
    store.mark_mined(conn, "/x.jsonl", 100.0)
    assert store.was_mined(conn, "/x.jsonl", 100.0)
    # an older mtime is still "seen"; a newer one asks to re-mine
    assert store.was_mined(conn, "/x.jsonl", 50.0)
    assert not store.was_mined(conn, "/x.jsonl", 150.0)
