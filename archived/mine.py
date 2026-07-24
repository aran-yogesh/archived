"""Backfill memories from history — past conversations and project dirs.

The capture hook only records sessions from the moment it is installed.
Mining reaches backwards: it walks the Claude Code transcripts already on
disk (and, optionally, a project's manifests and git log) and turns them
into the same fact/log memories, so a fresh install starts with context
instead of a blank store.

Conversation mining reuses the capture recipe — ask a small model
(`claude -p --model haiku`) for a compact capture JSON, then ingest it
through the normal dedup path so nothing near-identical is stored twice.
Each source is recorded in `mined_sources`, so re-running only does new
or changed work.
"""

import json
import os
import subprocess

from archived import cli, store

# Claude Code keeps per-project transcript JSONL here.
TRANSCRIPT_ROOT = os.path.expanduser("~/.claude/projects")
MAX_TRANSCRIPT_CHARS = 12000  # tail holds "where I left off"; match capture.py
MIN_TRANSCRIPT_CHARS = 400    # shorter than this = trivial, skip

PROMPT = """Read this coding session transcript and output ONLY a JSON object:
{"log": {"headline": "<what was done, <=15 words, telegraphic>",
         "body": "<key details, <=60 words>"},
 "facts": [{"headline": "<durable fact worth remembering across sessions>",
            "tags": ["<tag>"]}]}

Rules: facts = decisions, preferences, learnings only — NOT things derivable
from the code/git (structure, what a commit did) and NOT one-off mechanics.
Zero to three facts, usually zero or one. If the session was trivial
(greetings, one quick question), output {} instead. No markdown fences."""

# Live-session capture also records the "hot" slot (where you left off), which
# mining old transcripts skips — a finished past session has no current state.
CAPTURE_PROMPT = PROMPT.replace(
    '            "tags": ["<tag>"]}]}',
    '            "tags": ["<tag>"]}],\n'
    ' "hot": "<current state + exact next step, <=50 words>"}')

# files whose presence names a project and its primary language
_MANIFESTS = {
    "pyproject.toml": "Python", "setup.py": "Python", "Cargo.toml": "Rust",
    "go.mod": "Go", "package.json": "JavaScript/TypeScript",
    "pom.xml": "Java", "build.gradle": "Java/Kotlin", "Gemfile": "Ruby",
    "composer.json": "PHP",
}


def read_transcript(path):
    """Pull readable user/assistant text out of a transcript JSONL file."""
    lines = []
    with open(path) as handle:
        for raw in handle:
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if entry.get("type") not in ("user", "assistant"):
                continue
            content = (entry.get("message") or {}).get("content")
            if isinstance(content, str):
                lines.append(f"{entry['type']}: {content}")
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        lines.append(f"{entry['type']}: {block['text']}")
    return "\n".join(lines)[-MAX_TRANSCRIPT_CHARS:]


def summarize(transcript, prompt=PROMPT):
    """Ask haiku for a capture dict; None if the CLI is missing or fails.

    prompt defaults to the mining prompt (log + facts); live-session hooks
    pass CAPTURE_PROMPT so the current "hot" slot is captured too.
    """
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", "haiku"],
            input=transcript, capture_output=True, text=True, timeout=90)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _project_of(transcript_path):
    """Recover the project name Claude Code encoded in the transcript path.

    Claude Code stores transcripts under ~/.claude/projects/<slug>/ where
    the slug is the cwd with separators flattened; the last path segment is
    a good-enough project label for a mined log.
    """
    slug = os.path.basename(os.path.dirname(transcript_path))
    return slug.rstrip("-").split("-")[-1] or slug


def find_transcripts(since_days=None):
    """List transcript files under Claude Code's project dir, newest last.

    since_days limits to files modified within that many days (None = all).
    """
    if not os.path.isdir(TRANSCRIPT_ROOT):
        return []
    cutoff = 0.0
    if since_days is not None:
        import time
        cutoff = time.time() - since_days * 86400
    found = []
    for root, _dirs, files in os.walk(TRANSCRIPT_ROOT):
        for name in files:
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(root, name)
            mtime = os.path.getmtime(path)
            if mtime >= cutoff:
                found.append((mtime, path))
    return [p for _m, p in sorted(found)]


def mine_transcripts(conn, opts=None):
    """Mine past Claude Code transcripts into memories.

    opts keys: since_days (limit by age), limit (max files this run),
    dry_run (summarize but don't save). Returns a per-file result list of
    (path, status) where status is 'saved: ...', 'skipped', or 'trivial'.
    """
    opts = opts or {}
    paths = find_transcripts(opts.get("since_days"))
    results = []
    processed = 0
    for path in paths:
        if opts.get("limit") and processed >= opts["limit"]:
            break
        mtime = os.path.getmtime(path)
        if store.was_mined(conn, path, mtime):
            continue
        processed += 1
        transcript = read_transcript(path)
        if len(transcript) < MIN_TRANSCRIPT_CHARS:
            store.mark_mined(conn, path, mtime)
            results.append((path, "trivial"))
            continue
        capture = summarize(transcript)
        if not capture:
            results.append((path, "skipped"))  # leave unmarked: retry later
            continue
        capture["project"] = _project_of(path)
        status = "dry-run" if opts.get("dry_run") else cli.ingest(conn, capture)
        if not opts.get("dry_run"):
            store.mark_mined(conn, path, mtime)
        results.append((path, status))
    return results


def capture_transcript(conn, path, project=None):
    """Summarize one live session transcript and ingest it (hot included).

    Shared by the session-end, pre-compact, and periodic-save hooks. Reads
    the transcript, asks haiku for a log/facts/hot capture, and ingests it
    through the dedup path. Returns a short status string.
    """
    transcript = read_transcript(path)
    if len(transcript) < MIN_TRANSCRIPT_CHARS:
        return "trivial"
    capture = summarize(transcript, CAPTURE_PROMPT)
    if not capture:
        return "no summary"
    capture["project"] = project or _project_of(path)
    return cli.ingest(conn, capture)


def count_human_messages(path):
    """Count real user turns in a transcript (ignores tool-result messages).

    Drives the periodic-save hook, which fires a capture every N human
    messages. Tool results also arrive as 'user' entries, so only entries
    carrying human text (a string, or a list with a text block) are counted.
    """
    total = 0
    try:
        handle = open(path)
    except OSError:
        return 0
    with handle:
        for raw in handle:
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "user":
                continue
            content = (entry.get("message") or {}).get("content")
            if isinstance(content, str) and content.strip():
                total += 1
            elif isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "text"
                    for b in content):
                total += 1
    return total


def _git_contributors(root, top=5):
    """Top contributor names from git history ([] when not a repo)."""
    try:
        out = subprocess.run(
            ["git", "-C", root, "shortlog", "-sn", "--all", "--no-merges"],
            capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    names = []
    for line in out.stdout.splitlines():
        parts = line.strip().split("\t", 1)
        if len(parts) == 2:
            names.append(parts[1].strip())
    return names[:top]


def mine_project(conn, root):
    """Seed durable facts about a project from its manifests and git log.

    Saves at most a couple of facts (identity + languages, contributors),
    each through the dedup path, and returns what was saved.
    """
    root = os.path.abspath(root)
    name = os.path.basename(root)
    langs = sorted({lang for man, lang in _MANIFESTS.items()
                    if os.path.exists(os.path.join(root, man))})
    saved = []
    if langs:
        fact = {"headline": f"project {name} is a {'/'.join(langs)} project",
                "tags": ["project", "mined"], "project": name}
        if not store.find_similar(conn, fact):
            saved.append(f"fact #{store.save(conn, fact)}")
    people = _git_contributors(root)
    if people:
        fact = {"headline": f"{name} contributors: {', '.join(people)}",
                "tags": ["project", "people", "mined"], "project": name}
        if not store.find_similar(conn, fact):
            saved.append(f"fact #{store.save(conn, fact)}")
    return "saved: " + (", ".join(saved) if saved else "nothing new")
