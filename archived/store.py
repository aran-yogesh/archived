"""SQLite storage for archived memories.

All the token-saving work happens at write time: memories are stored as
compact headline + body rows, indexed by FTS5 and (optionally) embedded.
Search is hybrid: BM25 keyword ranking and cosine ranking over embeddings
are merged with reciprocal rank fusion, then small usage/project/tag/
recency boosts nudge the order without drowning out relevance.
"""

import os
import sqlite3

from archived import embed

DEFAULT_DB = os.path.expanduser("~/.archived/archived.db")

# fact = durable knowledge (deduped, never decays)
# log  = session diary entry (timestamped, rank decays after ~30 days)
# hot  = one per-project slot injected at session start (overwritten)
TYPES = ("fact", "log", "hot")

_RRF_K = 60        # standard reciprocal-rank-fusion constant
_POOL = 30         # candidates taken from each ranking before merging
_MIN_COSINE = 0.6         # ignore rows less related than this when searching
_DEDUP_MIN_COSINE = 0.8   # stricter bar for dedup: only near-identical facts

# boosts applied after the RRF merge. One list membership is worth at least
# 1/(_RRF_K + _POOL) ~= 0.011, and the boosts below sum to more than that,
# so the total is capped at _BOOST_CAP (< one membership). That keeps boosts
# to a tie-breaker between similarly-relevant hits — relevance still wins.
_BOOST_PROJECT = 0.008   # same project as the search
_BOOST_TAG = 0.006       # a query term exactly matches a tag
_BOOST_RECALL = 0.0005   # per recall, capped at 10 recalls
_BOOST_FRESH = 0.004     # fully fresh log entry, fades over 30 days
_BOOST_CAP = 0.01        # total boost stays below one RRF rank step

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('fact','log','hot')),
    headline TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT '',
    ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    superseded_by INTEGER REFERENCES memories(id),
    embedding BLOB,
    recall_count INTEGER NOT NULL DEFAULT 0,
    last_recalled TEXT
);
CREATE INDEX IF NOT EXISTS idx_mem_type_ts ON memories(type, ts DESC);
CREATE INDEX IF NOT EXISTS idx_mem_project ON memories(project);

CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(
    headline, body, tags,
    content='memories', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS mem_ai AFTER INSERT ON memories BEGIN
    INSERT INTO mem_fts(rowid, headline, body, tags)
    VALUES (new.id, new.headline, new.body, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS mem_ad AFTER DELETE ON memories BEGIN
    INSERT INTO mem_fts(mem_fts, rowid, headline, body, tags)
    VALUES ('delete', old.id, old.headline, old.body, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS mem_au AFTER UPDATE ON memories BEGIN
    INSERT INTO mem_fts(mem_fts, rowid, headline, body, tags)
    VALUES ('delete', old.id, old.headline, old.body, old.tags);
    INSERT INTO mem_fts(rowid, headline, body, tags)
    VALUES (new.id, new.headline, new.body, new.tags);
END;
"""

# columns every search candidate carries; the extras (tags, recall_count,
# freshness) feed the post-merge boosts and are stripped before returning
_HIT_COLUMNS = """m.id, m.type, m.headline, m.project, date(m.ts) AS day,
       m.tags, m.recall_count,
       CASE WHEN m.type = 'log'
            THEN max(0, 30 - (julianday('now') - julianday(m.ts))) / 30.0
            ELSE 0 END AS freshness"""


def _migrate(conn):
    """Add columns that databases created before them are missing."""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(memories)")}
    for col, spec in (("recall_count", "INTEGER NOT NULL DEFAULT 0"),
                      ("last_recalled", "TEXT")):
        if col not in have:
            conn.execute(f"ALTER TABLE memories ADD COLUMN {col} {spec}")


def connect(db_path=None):
    """Open (and create if needed) the archived database."""
    path = db_path or os.environ.get("ARCHIVED_DB", DEFAULT_DB)
    if path != ":memory:":
        os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def _norm_tags(tags):
    """Turn a tag list (or string) into one lowercase space-separated string."""
    if isinstance(tags, str):
        tags = tags.replace(",", " ").split()
    return " ".join(t.strip().lower() for t in tags if t.strip())


def _embed_input(headline, body, tags):
    """Join the searchable parts of a memory into one string to embed."""
    return "\n".join(p for p in (headline, body, tags) if p)


def save(conn, memory):
    """Insert a memory and return its id.

    memory keys: headline (required), body, type, tags, project,
    replace_id (marks an existing memory as superseded by this one).
    """
    mtype = memory.get("type", "fact")
    if mtype not in TYPES:
        raise ValueError(f"type must be one of {TYPES}")
    if not memory.get("headline", "").strip():
        raise ValueError("headline is required")
    headline = memory["headline"].strip()
    body = memory.get("body", "").strip()
    tags = _norm_tags(memory.get("tags", ""))
    blob = None
    if mtype != "hot":
        blob = embed.embed_text(_embed_input(headline, body, tags))
    cur = conn.execute(
        "INSERT INTO memories (type, headline, body, tags, project, embedding) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (mtype, headline, body, tags, memory.get("project", "").strip(), blob),
    )
    new_id = cur.lastrowid
    replace_id = memory.get("replace_id")
    if replace_id:
        conn.execute(
            "UPDATE memories SET superseded_by = ? WHERE id = ?",
            (new_id, replace_id),
        )
    conn.commit()
    return new_id


def _terms(text):
    """Split free text into lowercase search terms (alnum runs, len > 1)."""
    cleaned = "".join(c if c.isalnum() else " " for c in text.lower())
    return [t for t in cleaned.split() if len(t) > 1][:12]


def _fts_query(text):
    """Build a safe OR-of-terms FTS5 query from free text."""
    return " OR ".join(f'"{t}"' for t in _terms(text))


def _filter_sql(opts):
    """Build the WHERE additions both rankings share (hard filters)."""
    sql, params = "", []
    if opts.get("project_filter"):
        sql += " AND m.project = ?"
        params.append(opts["project_filter"])
    if opts.get("mem_type"):
        sql += " AND m.type = ?"
        params.append(opts["mem_type"])
    return sql, params


def _keyword_hits(conn, query, opts):
    """Rank memories by weighted BM25 (headline 3x, tags 2x, body 1x)
    plus a recency lift for logs; returns the top candidates."""
    fts = _fts_query(query)
    if not fts:
        return []
    extra, params = _filter_sql(opts)
    sql = f"""
    SELECT {_HIT_COLUMNS},
           -bm25(mem_fts, 3.0, 1.0, 2.0)
           + CASE WHEN m.type = 'log'
                  THEN max(0, 30 - (julianday('now') - julianday(m.ts))) / 15.0
                  ELSE 0 END AS score
    FROM mem_fts JOIN memories m ON m.id = mem_fts.rowid
    WHERE mem_fts MATCH ? AND m.superseded_by IS NULL AND m.type != 'hot'
    {extra} ORDER BY score DESC LIMIT ?"""
    return [dict(r) for r in conn.execute(sql, [fts, *params, _POOL])]


def _semantic_hits(conn, query, opts):
    """Rank embedded memories by cosine similarity to the query; empty
    when embeddings are unavailable or nothing is related enough.

    opts["min_cosine"] sets the relatedness bar (default _MIN_COSINE);
    the dedup path raises it so only near-identical facts match.

    This scans every embedded row and scores it in Python (no ANN index).
    That is O(rows) per search — fine for a personal store of hundreds to
    low thousands; a store an order of magnitude larger would want an
    approximate-nearest-neighbour index here instead.
    """
    blob = embed.embed_query(query)
    if blob is None:
        return []
    qvec = embed.to_vector(blob)
    min_cosine = opts.get("min_cosine", _MIN_COSINE)
    extra, params = _filter_sql(opts)
    sql = f"""
    SELECT {_HIT_COLUMNS}, m.embedding FROM memories m
    WHERE m.superseded_by IS NULL AND m.type != 'hot'
      AND m.embedding IS NOT NULL{extra}"""
    hits = []
    for row in conn.execute(sql, params):
        hit = dict(row)
        sim = embed.cosine(qvec, embed.to_vector(hit.pop("embedding")))
        if sim >= min_cosine:
            hit["score"] = sim
            hits.append(hit)
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:_POOL]


def _rrf(keyword, semantic):
    """Merge two ranked hit lists with reciprocal rank fusion."""
    merged = {}
    for hits in (keyword, semantic):
        for rank, hit in enumerate(hits, 1):
            entry = merged.setdefault(hit["id"], {**hit, "score": 0.0})
            entry["score"] += 1.0 / (_RRF_K + rank)
    return list(merged.values())


def _boosts(hit, query, opts):
    """Small post-merge rank boosts: recalled often, same project as the
    search, a query term matching a tag, and freshness for logs. The total
    is capped at _BOOST_CAP (below one RRF rank step) so the boosts only
    break ties between similarly-relevant hits — relevance still dominates."""
    boost = min(hit["recall_count"], 10) * _BOOST_RECALL
    if opts.get("project") and hit["project"] == opts["project"]:
        boost += _BOOST_PROJECT
    tags = set(hit["tags"].split())
    if tags and not tags.isdisjoint(_terms(query)):
        boost += _BOOST_TAG
    boost += hit["freshness"] * _BOOST_FRESH
    return min(boost, _BOOST_CAP)


def search(conn, query, opts=None):
    """Hybrid search; returns token-lean headline rows, best first.

    opts keys: limit (default 5), project (soft rank boost),
    project_filter (hard filter), mem_type. Keyword (BM25) and semantic
    (embedding cosine) rankings are merged with RRF, then boosted.
    """
    opts = opts or {}
    merged = _rrf(_keyword_hits(conn, query, opts),
                  _semantic_hits(conn, query, opts))
    for hit in merged:
        hit["score"] += _boosts(hit, query, opts)
    merged.sort(key=lambda h: h["score"], reverse=True)
    keep = ("id", "type", "headline", "project", "day", "score")
    return [{k: h[k] for k in keep} for h in merged[:opts.get("limit", 5)]]


def find_similar(conn, memory):
    """Find existing facts similar to a new one, for the dedup loop.

    Uses a stricter cosine bar (_DEDUP_MIN_COSINE) than normal search so
    that automated ingest only skips near-identical facts, not merely
    topically-related ones — dropping a genuinely new fact is worse here
    than keeping a near-duplicate, since ingest has no human to confirm.
    """
    text = memory.get("headline", "") + " " + _norm_tags(memory.get("tags", ""))
    hits = search(conn, text, {"mem_type": "fact", "limit": 3,
                               "project_filter": memory.get("project") or None,
                               "min_cosine": _DEDUP_MIN_COSINE})
    return [h for h in hits if h["score"] > 0]


def mark_recalled(conn, ids):
    """Bump recall counters for memories that were just read in full."""
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE memories SET recall_count = recall_count + 1, "
        f"last_recalled = strftime('%Y-%m-%dT%H:%M:%SZ','now') "
        f"WHERE id IN ({marks})", list(ids))
    conn.commit()


def backfill_embeddings(conn):
    """Embed memories saved without embeddings; returns how many got one."""
    rows = conn.execute(
        "SELECT id, headline, body, tags FROM memories "
        "WHERE embedding IS NULL AND type != 'hot'").fetchall()
    filled = 0
    for row in rows:
        blob = embed.embed_text(
            _embed_input(row["headline"], row["body"], row["tags"]))
        if blob is None:
            break  # embeddings unavailable — leave the rest for later
        conn.execute("UPDATE memories SET embedding = ? WHERE id = ?",
                     (blob, row["id"]))
        filled += 1
    conn.commit()
    return filled


def get(conn, mem_id):
    """Fetch one full memory by id (None if missing)."""
    row = conn.execute(
        "SELECT id, type, headline, body, tags, project, ts, superseded_by, "
        "recall_count, last_recalled FROM memories WHERE id = ?",
        (mem_id,),
    ).fetchone()
    return dict(row) if row else None


def recent(conn, opts=None):
    """List recent log entries, newest first (the session diary)."""
    opts = opts or {}
    sql = ("SELECT id, headline, project, date(ts) AS day FROM memories "
           "WHERE type = 'log' AND superseded_by IS NULL")
    params = []
    if opts.get("project"):
        sql += " AND project = ?"
        params.append(opts["project"])
    sql += " ORDER BY ts DESC, id DESC LIMIT ?"
    params.append(opts.get("limit", 7))
    return [dict(r) for r in conn.execute(sql, params)]


def set_hot(conn, project, content):
    """Overwrite the per-project hot slot (current state + leave-off)."""
    conn.execute("DELETE FROM memories WHERE type = 'hot' AND project = ?",
                 (project,))
    conn.execute(
        "INSERT INTO memories (type, headline, body, project) "
        "VALUES ('hot', ?, ?, ?)",
        (f"hot context: {project}", content.strip(), project),
    )
    conn.commit()


def get_hot(conn, project):
    """Read the hot slot for a project ('' if none)."""
    row = conn.execute(
        "SELECT body FROM memories WHERE type = 'hot' AND project = ? "
        "ORDER BY ts DESC LIMIT 1",
        (project,),
    ).fetchone()
    return row["body"] if row else ""
