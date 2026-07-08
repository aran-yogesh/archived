"""Tests for hybrid retrieval: embeddings, RRF merge, and ranking boosts."""

import sqlite3
import zlib
from array import array

import pytest

from archived import embed, store


@pytest.fixture
def conn():
    """Fresh in-memory database for each test."""
    c = store.connect(":memory:")
    yield c
    c.close()


def _fake_embed(text):
    """Deterministic tiny stand-in embedding (no fastembed needed)."""
    vec = [0.0] * 8
    for tok in text.lower().split():
        vec[zlib.crc32(tok.encode()) % 8] += 1.0
    return array("f", vec).tobytes()


def _stored_embedding(conn, mem_id):
    """Read a memory's raw embedding blob straight from the database."""
    row = conn.execute("SELECT embedding FROM memories WHERE id = ?",
                       (mem_id,)).fetchone()
    return row["embedding"]


# --- Part A: semantic search --------------------------------------------

def test_paraphrase_recall(conn):
    """A paraphrase with no keyword overlap is found via embeddings."""
    if embed.embed_text("probe") is None:
        pytest.skip("fastembed unavailable — semantic path can't run")
    store.save(conn, {"headline": "deploys fail on Node 20"})
    hits = store.search(conn, "release broken after runtime upgrade")
    assert any(h["headline"] == "deploys fail on Node 20" for h in hits)


def test_exact_identifier_ranks_first(conn):
    store.save(conn, {"headline": "search uses OR of quoted terms"})
    mid = store.save(conn, {"headline": "refactored _fts_query to escape terms"})
    hits = store.search(conn, "_fts_query")
    assert hits and hits[0]["id"] == mid


def test_fallback_keyword_only(conn, monkeypatch):
    """With embeddings unavailable, save and search still work."""
    monkeypatch.setattr(embed, "embed_text", lambda text: None)
    monkeypatch.setattr(embed, "embed_query", lambda text: None)
    mid = store.save(conn, {"headline": "uses ruff for linting"})
    assert [h["id"] for h in store.search(conn, "ruff linting")] == [mid]
    assert _stored_embedding(conn, mid) is None


def test_backfill_fills_only_missing(conn, monkeypatch):
    monkeypatch.setattr(embed, "embed_text", lambda text: None)
    a = store.save(conn, {"headline": "first saved without embedding"})
    b = store.save(conn, {"headline": "second saved without embedding"})
    monkeypatch.setattr(embed, "embed_text", lambda t: _fake_embed("v1 " + t))
    c = store.save(conn, {"headline": "third saved with embedding"})
    old_blob = _stored_embedding(conn, c)

    monkeypatch.setattr(embed, "embed_text", lambda t: _fake_embed("v2 " + t))
    assert store.backfill_embeddings(conn) == 2
    assert _stored_embedding(conn, a) is not None
    assert _stored_embedding(conn, b) is not None
    assert _stored_embedding(conn, c) == old_blob  # untouched
    assert store.backfill_embeddings(conn) == 0    # nothing left to fill


def test_hot_memories_never_embedded(conn):
    store.set_hot(conn, "api", "left off: rate limiter")
    row = conn.execute("SELECT embedding FROM memories WHERE type = 'hot'"
                       ).fetchone()
    assert row["embedding"] is None
    assert store.backfill_embeddings(conn) == 0


# --- Part B: ranking boosts ----------------------------------------------

def test_mark_recalled_bumps_ranking(conn):
    store.save(conn, {"headline": "kafka consumer lag fix"})
    store.save(conn, {"headline": "kafka consumer lag fix"})
    loser = store.search(conn, "kafka consumer lag")[-1]["id"]
    for _ in range(3):
        store.mark_recalled(conn, [loser])
    assert store.search(conn, "kafka consumer lag")[0]["id"] == loser
    assert store.get(conn, loser)["recall_count"] == 3
    assert store.get(conn, loser)["last_recalled"]


def test_project_soft_boost_not_hard_filter(conn):
    api = store.save(conn, {"headline": "grafana dashboard setup",
                            "project": "api"})
    web = store.save(conn, {"headline": "grafana dashboard setup",
                            "project": "web"})
    hits = store.search(conn, "grafana dashboard", {"project": "api"})
    assert {h["id"] for h in hits} == {api, web}  # cross-project still surfaces
    assert hits[0]["id"] == api                   # same project ranks first


def test_dedup_hard_filters_by_project(conn):
    store.save(conn, {"headline": "uses pnpm not npm", "project": "alpha"})
    candidate = {"headline": "uses pnpm not npm", "project": "beta"}
    assert store.find_similar(conn, candidate) == []
    assert store.find_similar(conn, {**candidate, "project": "alpha"})


def test_tag_match_outranks_body_only(conn):
    tagged = store.save(conn, {"headline": "cache layer notes",
                               "tags": ["redis"]})
    store.save(conn, {"headline": "cache layer notes",
                      "body": "we may try redis"})
    assert store.search(conn, "redis")[0]["id"] == tagged


def test_migration_adds_recall_columns(tmp_path):
    """A database from before the recall columns opens and works."""
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    raw.executescript("""
    CREATE TABLE memories (
        id INTEGER PRIMARY KEY,
        type TEXT NOT NULL CHECK (type IN ('fact','log','hot')),
        headline TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        tags TEXT NOT NULL DEFAULT '',
        project TEXT NOT NULL DEFAULT '',
        ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
        superseded_by INTEGER REFERENCES memories(id),
        embedding BLOB
    );
    CREATE VIRTUAL TABLE mem_fts USING fts5(
        headline, body, tags, content='memories', content_rowid='id'
    );
    CREATE TRIGGER mem_ai AFTER INSERT ON memories BEGIN
        INSERT INTO mem_fts(rowid, headline, body, tags)
        VALUES (new.id, new.headline, new.body, new.tags);
    END;
    CREATE TRIGGER mem_au AFTER UPDATE ON memories BEGIN
        INSERT INTO mem_fts(mem_fts, rowid, headline, body, tags)
        VALUES ('delete', old.id, old.headline, old.body, old.tags);
        INSERT INTO mem_fts(rowid, headline, body, tags)
        VALUES (new.id, new.headline, new.body, new.tags);
    END;""")
    raw.execute("INSERT INTO memories (type, headline) "
                "VALUES ('fact', 'pre-upgrade memory')")
    raw.commit()
    raw.close()

    conn = store.connect(path)
    assert store.get(conn, 1)["recall_count"] == 0
    store.mark_recalled(conn, [1])
    assert store.get(conn, 1)["recall_count"] == 1
    conn.close()
