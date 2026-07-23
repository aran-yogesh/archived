"""archived CLI — used by Claude Code hooks and for manual inspection.

Commands:
  archived hot <project>        print the hot slot (SessionStart hook)
  archived ingest               read a capture JSON from stdin (SessionEnd)
  archived search <query...>    search from the terminal
  archived recent               show the session diary
  archived backfill             embed memories missing embeddings
  archived doctor               show where data lives and what's in it
"""

import argparse
import json
import os
import sys

from archived import embed, store


def _cmd_hot(conn, args):
    """Print a project's hot context so a hook can inject it."""
    content = store.get_hot(conn, args.project)
    if content:
        print(content)


def ingest(conn, data):
    """Ingest one session-end capture dict; returns a summary of what saved.

    Shape: {"project": str,
            "log": {"headline": str, "body": str},
            "facts": [{"headline": str, "body": str, "tags": [...]}],
            "hot": str}
    Facts that look like duplicates of existing ones are skipped.
    """
    project = data.get("project", "")
    saved = []
    log = data.get("log")
    if log and log.get("headline"):
        mid = store.save(conn, {**log, "type": "log", "project": project})
        saved.append(f"log #{mid}")
    for fact in data.get("facts", []):
        if not fact.get("headline"):
            continue
        fact = {**fact, "type": "fact", "project": project}
        if store.find_similar(conn, fact):
            continue  # near-duplicate: NOOP
        saved.append(f"fact #{store.save(conn, fact)}")
    if data.get("hot"):
        store.set_hot(conn, project, data["hot"])
        saved.append("hot")
    return "saved: " + (", ".join(saved) if saved else "nothing")


def _cmd_ingest(conn, args):
    """CLI wrapper: read the capture JSON from stdin and ingest it."""
    print(ingest(conn, json.load(sys.stdin)))


def _cmd_search(conn, args):
    """Search memories from the terminal (debugging / curiosity)."""
    hits = store.search(conn, " ".join(args.query), {"limit": args.limit})
    for h in hits:
        print(f"#{h['id']} [{h['type']}] {h['headline']} — {h['day']}")
    if not hits:
        print("no matches")


def _cmd_recent(conn, args):
    """Show recent session logs."""
    for e in store.recent(conn, {"limit": args.limit}):
        print(f"#{e['id']} {e['day']}: {e['headline']}")


def _cmd_backfill(conn, args):
    """Embed memories that were saved without embeddings."""
    print(f"embedded {store.backfill_embeddings(conn)} memories")


def _cmd_doctor(conn, args):
    """Print a health summary so a user can see the setup is working."""
    st = store.stats(conn)
    by = st["by_type"]
    path = store.db_path()
    size_kb = os.path.getsize(path) / 1024 if os.path.exists(path) else 0
    embeddings = "on" if embed.available() else "off (keyword-only)"
    print("archived doctor")
    print(f"  db:           {path} ({size_kb:.0f} KB)")
    print(f"  memories:     {st['total']} "
          f"(facts {by.get('fact', 0)}, logs {by.get('log', 0)}, "
          f"hot {by.get('hot', 0)})")
    print(f"  embeddings:   {embeddings}, {st['missing_embeddings']} missing")
    print(f"  last capture: {st['last_capture'] or 'never'}")


def main():
    """Parse arguments and dispatch to a subcommand."""
    p = argparse.ArgumentParser(prog="archived")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("hot", help="print a project's hot context")
    sp.add_argument("project")
    sp.set_defaults(fn=_cmd_hot)

    sp = sub.add_parser("ingest", help="ingest capture JSON from stdin")
    sp.set_defaults(fn=_cmd_ingest)

    sp = sub.add_parser("search", help="search memories")
    sp.add_argument("query", nargs="+")
    sp.add_argument("--limit", type=int, default=5)
    sp.set_defaults(fn=_cmd_search)

    sp = sub.add_parser("recent", help="show session diary")
    sp.add_argument("--limit", type=int, default=7)
    sp.set_defaults(fn=_cmd_recent)

    sp = sub.add_parser("backfill", help="embed memories missing embeddings")
    sp.set_defaults(fn=_cmd_backfill)

    sp = sub.add_parser("doctor", help="show where data lives and what's in it")
    sp.set_defaults(fn=_cmd_doctor)

    args = p.parse_args()
    conn = store.connect()
    try:
        args.fn(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
