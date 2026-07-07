"""archived CLI — used by Claude Code hooks and for manual inspection.

Commands:
  archived hot <project>        print the hot slot (SessionStart hook)
  archived ingest               read a capture JSON from stdin (SessionEnd)
  archived search <query...>    search from the terminal
  archived recent               show the session diary
"""

import argparse
import json
import sys

from archived import store


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

    args = p.parse_args()
    conn = store.connect()
    try:
        args.fn(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
