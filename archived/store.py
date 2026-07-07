"""SQLite storage for archived memories.

All the token-saving work happens at write time: memories are stored as
compact headline + body rows, indexed by FTS5, and searched with a
combined score (keyword relevance + tag match + recency for logs).
"""

import os
import sqlite3

DEFAULT_DB = os.path.expanduser("~/.archived/archived.db")

# fact = durable knowledge (deduped, never decays)
# log  = session diary entry (timestamped, rank decays after ~30 days)
# hot  = one per-project slot injected at session start (overwritten)
TYPES = ("fact", "log", "hot")

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
    embedding BLOB
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


def connect(db_path=None):
    """Open (and create if needed) the archived database."""
    path = db_path or os.environ.get("ARCHIVED_DB", DEFAULT_DB)
    if path != ":memory:":
        os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _norm_tags(tags):
    """Turn a tag list (or string) into one lowercase space-separated string."""
    if isinstance(tags, str):
        tags = tags.replace(",", " ").split()
    return " ".join(t.strip().lower() for t in tags if t.strip())


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
    cur = conn.execute(
        "INSERT INTO memories (type, headline, body, tags, project) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            mtype,
            memory["headline"].strip(),
            memory.get("body", "").strip(),
            _norm_tags(memory.get("tags", "")),
            memory.get("project", "").strip(),
        ),
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


def _fts_query(text):
    """Build a safe OR-of-terms FTS5 query from free text."""
    terms = [t for t in "".join(c if c.isalnum() else " " for c in text).split() if len(t) > 1]
    return " OR ".join(f'"{t}"' for t in terms[:12])


def search(conn, query, opts=None):
    """Search memories; returns token-lean headline rows, best first.

    opts keys: limit (default 5), project, mem_type.
    Score = weighted BM25 (headline 3x, tags 2x, body 1x) plus a recency
    boost for logs that fades to zero over 30 days.
    """
    opts = opts or {}
    fts = _fts_query(query)
    if not fts:
        return []
    sql = """
    SELECT m.id, m.type, m.headline, m.project, date(m.ts) AS day,
           -bm25(mem_fts, 3.0, 1.0, 2.0)
           + CASE WHEN m.type = 'log'
                  THEN max(0, 30 - (julianday('now') - julianday(m.ts))) / 15.0
                  ELSE 0 END AS score
    FROM mem_fts JOIN memories m ON m.id = mem_fts.rowid
    WHERE mem_fts MATCH ? AND m.superseded_by IS NULL AND m.type != 'hot'
    """
    params = [fts]
    if opts.get("project"):
        sql += " AND m.project = ?"
        params.append(opts["project"])
    if opts.get("mem_type"):
        sql += " AND m.type = ?"
        params.append(opts["mem_type"])
    sql += " ORDER BY score DESC LIMIT ?"
    params.append(opts.get("limit", 5))
    return [dict(r) for r in conn.execute(sql, params)]


def find_similar(conn, memory):
    """Find existing facts similar to a new one, for the dedup loop."""
    text = memory.get("headline", "") + " " + _norm_tags(memory.get("tags", ""))
    hits = search(conn, text, {"mem_type": "fact", "limit": 3,
                               "project": memory.get("project") or None})
    return [h for h in hits if h["score"] > 0]


def get(conn, mem_id):
    """Fetch one full memory by id (None if missing)."""
    row = conn.execute(
        "SELECT id, type, headline, body, tags, project, ts, superseded_by "
        "FROM memories WHERE id = ?",
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
