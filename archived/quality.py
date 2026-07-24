"""Maintenance passes over an existing store — dedup and a health report.

archived already blocks near-duplicate facts *at write time* (see
store.find_similar). These passes clean up a store that already grew:
merge facts that ended up near-identical anyway (different wording, or
saved before embeddings were on), and surface anything that looks off.

Nothing here deletes: dedup supersedes the older fact (keeping the newer
one live), so history stays intact and the change is auditable.
"""

from archived import embed, store

# same bar store.find_similar uses at write time, so an interactive dedup
# agrees with what ingest would have blocked.
_DUP_COSINE = 0.8


def _vectors(facts):
    """Decode embeddings for facts that have one; skip those that don't."""
    out = []
    for fact in facts:
        blob = fact.get("embedding")
        if blob is not None:
            out.append((fact, embed.to_vector(blob)))
    return out


def duplicate_clusters(conn):
    """Group live facts that are near-identical into clusters of ids.

    Compares every embedded fact against every other (O(n^2), fine for a
    personal store) and returns only groups with more than one member,
    each a list of ids oldest-first. Facts without embeddings can't be
    compared and never cluster — run `archived backfill` first.
    """
    vecs = _vectors(store.all_facts(conn))
    parent = {f["id"]: f["id"] for f, _v in vecs}

    def find(node):
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for i in range(len(vecs)):
        fact_a, vec_a = vecs[i]
        for j in range(i + 1, len(vecs)):
            fact_b, vec_b = vecs[j]
            if embed.cosine(vec_a, vec_b) >= _DUP_COSINE:
                parent[find(fact_b["id"])] = find(fact_a["id"])

    groups = {}
    for fact, _v in vecs:
        groups.setdefault(find(fact["id"]), []).append(fact["id"])
    return [sorted(ids) for ids in groups.values() if len(ids) > 1]


def dedup(conn, apply=False):
    """Report (and optionally merge) near-duplicate fact clusters.

    For each cluster the newest fact (highest id) is kept live and the
    older ones are superseded by it. With apply=False nothing changes —
    the returned clusters are a preview. Returns the list of clusters
    acted on (or that would be).
    """
    clusters = duplicate_clusters(conn)
    if apply:
        for ids in clusters:
            keep = max(ids)
            for old in ids:
                if old != keep:
                    store.supersede(conn, old, keep)
    return clusters


def doctor(conn):
    """Collect a health snapshot of the store for `archived doctor`.

    Returns a dict: counts by type, embedding coverage, duplicate-cluster
    count, facts with an empty body, and dangling supersede pointers
    (superseded_by referencing a row that no longer exists).
    """
    stats = store.counts(conn)
    empty_bodies = conn.execute(
        "SELECT count(*) AS n FROM memories WHERE type = 'fact' "
        "AND superseded_by IS NULL AND body = ''").fetchone()["n"]
    dangling = conn.execute(
        "SELECT count(*) AS n FROM memories m WHERE m.superseded_by IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM memories t WHERE t.id = m.superseded_by)"
    ).fetchone()["n"]
    mined = conn.execute(
        "SELECT count(*) AS n FROM mined_sources").fetchone()["n"]
    return {
        "by_type": stats["by_type"],
        "embedded": stats["embedded"],
        "embeddable": stats["embeddable"],
        "duplicate_clusters": len(duplicate_clusters(conn)),
        "empty_body_facts": empty_bodies,
        "dangling_supersedes": dangling,
        "mined_sources": mined,
    }
