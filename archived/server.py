"""archived MCP server.

Exposes the memory bucket to any MCP client (Claude Code, Codex, Cursor...).
Tool results are compact plain text — headlines first, bodies only on
request — so retrieval stays cheap for the model reading them.
"""

from mcp.server.fastmcp import FastMCP

from archived import store

mcp = FastMCP("archived")
_conn = None


def _db():
    """Lazily open one shared database connection."""
    global _conn
    if _conn is None:
        _conn = store.connect()
    return _conn


def _hit_line(hit):
    """Format one search hit as a single token-lean line."""
    proj = f" ({hit['project']})" if hit.get("project") else ""
    return f"#{hit['id']} [{hit['type']}] {hit['headline']}{proj} — {hit['day']}"


@mcp.tool()
def save_memory(headline: str, body: str = "", meta: dict | None = None) -> str:
    """Save a memory. headline: one telegraphic line (<=15 words), body:
    details (<=100 tokens, optional). meta options: type ('fact' default,
    or 'log'), tags (list), project (str), replace_id (int: supersede an
    outdated memory), force (bool: save even if similar memories exist).

    If similar facts already exist you get them back instead of saving —
    then decide: skip (duplicate), replace_id (update), or force (new).
    """
    meta = meta or {}
    memory = {
        "headline": headline,
        "body": body,
        "type": meta.get("type", "fact"),
        "tags": meta.get("tags", []),
        "project": meta.get("project", ""),
        "replace_id": meta.get("replace_id"),
    }
    is_plain_fact = (memory["type"] == "fact"
                     and not memory["replace_id"] and not meta.get("force"))
    if is_plain_fact:
        similar = store.find_similar(_db(), memory)
        if similar:
            lines = "\n".join(_hit_line(h) for h in similar)
            return ("NOT SAVED — similar memories exist:\n" + lines +
                    "\nIf duplicate: skip. If this updates one: retry with "
                    "meta.replace_id=<id>. If genuinely new: retry with "
                    "meta.force=true.")
    new_id = store.save(_db(), memory)
    return f"Saved #{new_id}"


@mcp.tool()
def search_memory(query: str, project: str = "", limit: int = 5) -> str:
    """Search memories by keywords. Returns headline lines only (cheap);
    call get_memory(id) to expand one. Filter by project if given."""
    hits = store.search(_db(), query,
                        {"project": project or None, "limit": limit})
    if not hits:
        return "No memories found."
    return "\n".join(_hit_line(h) for h in hits)


@mcp.tool()
def get_memory(memory_id: int) -> str:
    """Fetch one memory's full details by id (from search/recent results)."""
    mem = store.get(_db(), memory_id)
    if not mem:
        return f"No memory #{memory_id}."
    # Reading a memory is intentionally a write: it bumps the recall counter
    # so often-used memories rank higher later. The single-user MCP server is
    # serial, so the extra commit per read is harmless here.
    store.mark_recalled(_db(), [memory_id])
    parts = [f"#{mem['id']} [{mem['type']}] {mem['headline']}"]
    if mem["body"]:
        parts.append(mem["body"])
    if mem["tags"]:
        parts.append(f"tags: {mem['tags']}")
    if mem["project"]:
        parts.append(f"project: {mem['project']}")
    parts.append(f"saved: {mem['ts']}")
    if mem["superseded_by"]:
        parts.append(f"OUTDATED — superseded by #{mem['superseded_by']}")
    return "\n".join(parts)


@mcp.tool()
def recent_memories(project: str = "", limit: int = 7) -> str:
    """List recent session logs (the diary) — what was worked on, newest
    first. Filter by project if given."""
    entries = store.recent(_db(), {"project": project or None, "limit": limit})
    if not entries:
        return "No session logs yet."
    return "\n".join(
        f"#{e['id']} {e['day']}: {e['headline']}"
        + (f" ({e['project']})" if e["project"] else "")
        for e in entries
    )


def main():
    """Run the server over stdio (what MCP clients expect)."""
    mcp.run()


if __name__ == "__main__":
    main()
