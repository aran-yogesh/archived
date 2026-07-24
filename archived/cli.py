"""archived CLI — used by Claude Code hooks and for manual inspection.

Commands:
  archived hot <project>        print the hot slot (SessionStart hook)
  archived ingest               read a capture JSON from stdin (SessionEnd)
  archived save <headline>      save one memory (shell hooks / manual)
  archived search <query...>    search from the terminal
  archived recent               show the session diary
  archived backfill             embed memories missing embeddings
  archived capture <path>       summarize + ingest one transcript file
  archived mine                 backfill memories from past transcripts
  archived mine-project <dir>   seed facts from a project's manifests/git
  archived dedup                merge near-duplicate facts (--apply to write)
  archived doctor               print a store health report
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


def _cmd_save(conn, args):
    """Save one memory from the terminal (used by the shell save hook)."""
    body = sys.stdin.read().strip() if args.stdin_body else args.body
    memory = {"headline": args.headline, "body": body or "",
              "type": args.type, "tags": args.tag, "project": args.project}
    if args.type == "fact" and not args.force and store.find_similar(conn, memory):
        print("not saved: similar fact exists (use --force to override)")
        return
    print(f"saved #{store.save(conn, memory)}")


def _cmd_capture(conn, args):
    """Summarize one transcript file and ingest it (generic session-end hook).

    Lets any tool that can name its transcript on session end reuse the
    same summarize-then-ingest path the Claude Code plugin uses.
    """
    import os

    from archived import mine
    if not os.path.exists(args.path):
        print(f"no transcript at {args.path}")
        return
    print(mine.capture_transcript(conn, args.path, args.project or None))


def _cmd_mine(conn, args):
    """Mine past Claude Code transcripts into memories."""
    from archived import mine
    opts = {"since_days": args.since, "limit": args.limit, "dry_run": args.dry_run}
    results = mine.mine_transcripts(conn, opts)
    for path, status in results:
        print(f"{status}: {path}")
    print(f"— {len(results)} transcript(s) processed")


def _cmd_mine_project(conn, args):
    """Seed durable facts about a project from its manifests and git log."""
    from archived import mine
    print(mine.mine_project(conn, args.dir))


def _cmd_dedup(conn, args):
    """Preview or merge near-duplicate fact clusters."""
    from archived import quality
    clusters = quality.dedup(conn, apply=args.apply)
    for ids in clusters:
        keep = max(ids)
        merged = ", ".join(f"#{i}" for i in ids if i != keep)
        verb = "superseded" if args.apply else "would supersede"
        print(f"keep #{keep}; {verb} {merged}")
    action = "merged" if args.apply else "found (dry run; use --apply)"
    print(f"— {len(clusters)} duplicate cluster(s) {action}")


def _cmd_doctor(conn, args):
    """Print a health report for the store."""
    from archived import quality
    rep = quality.doctor(conn)
    counts = ", ".join(f"{t}={n}" for t, n in rep["by_type"].items())
    print(f"memories: {counts}")
    print(f"embeddings: {rep['embedded']}/{rep['embeddable']} embeddable rows")
    print(f"duplicate clusters: {rep['duplicate_clusters']}")
    print(f"facts with empty body: {rep['empty_body_facts']}")
    print(f"dangling supersede pointers: {rep['dangling_supersedes']}")
    print(f"mined sources: {rep['mined_sources']}")


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


def main():
    """Parse arguments and dispatch to a subcommand."""
    p = argparse.ArgumentParser(prog="archived")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("hot", help="print a project's hot context")
    sp.add_argument("project")
    sp.set_defaults(fn=_cmd_hot)

    sp = sub.add_parser("ingest", help="ingest capture JSON from stdin")
    sp.set_defaults(fn=_cmd_ingest)

    sp = sub.add_parser("save", help="save one memory")
    sp.add_argument("headline")
    sp.add_argument("body", nargs="?", default="")
    sp.add_argument("--stdin-body", action="store_true",
                    help="read body from stdin instead of the argument")
    sp.add_argument("--type", choices=store.TYPES, default="fact")
    sp.add_argument("--tag", action="append", default=[])
    sp.add_argument("--project", default="")
    sp.add_argument("--force", action="store_true", help="save even if similar")
    sp.set_defaults(fn=_cmd_save)

    sp = sub.add_parser("search", help="search memories")
    sp.add_argument("query", nargs="+")
    sp.add_argument("--limit", type=int, default=5)
    sp.set_defaults(fn=_cmd_search)

    sp = sub.add_parser("capture", help="summarize + ingest one transcript file")
    sp.add_argument("path")
    sp.add_argument("--project", default="")
    sp.set_defaults(fn=_cmd_capture)

    sp = sub.add_parser("mine", help="backfill memories from past transcripts")
    sp.add_argument("--since", type=int, default=None,
                    help="only transcripts modified within N days")
    sp.add_argument("--limit", type=int, default=None,
                    help="process at most N new transcripts this run")
    sp.add_argument("--dry-run", action="store_true",
                    help="summarize but don't save")
    sp.set_defaults(fn=_cmd_mine)

    sp = sub.add_parser("mine-project", help="seed facts from a project dir")
    sp.add_argument("dir")
    sp.set_defaults(fn=_cmd_mine_project)

    sp = sub.add_parser("dedup", help="merge near-duplicate facts")
    sp.add_argument("--apply", action="store_true",
                    help="write changes (default is a dry-run preview)")
    sp.set_defaults(fn=_cmd_dedup)

    sp = sub.add_parser("doctor", help="print a store health report")
    sp.set_defaults(fn=_cmd_doctor)

    sp = sub.add_parser("recent", help="show session diary")
    sp.add_argument("--limit", type=int, default=7)
    sp.set_defaults(fn=_cmd_recent)

    sp = sub.add_parser("backfill", help="embed memories missing embeddings")
    sp.set_defaults(fn=_cmd_backfill)

    args = p.parse_args()
    conn = store.connect()
    try:
        args.fn(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
