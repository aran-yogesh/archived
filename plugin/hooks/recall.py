#!/usr/bin/env python3
"""UserPromptSubmit hook: inject memories relevant to the incoming prompt.

Searches archived with the prompt text and prints up to three matching
headlines — stdout from a UserPromptSubmit hook is added to Claude's
context. Prints nothing when nothing matches (a miss costs 0 tokens),
skips trivial prompts, and never repeats an id within one session.
All failures are silent — a broken hook must never disturb the user —
but get logged to ~/.archived/hook.log for debugging.
"""

import contextlib
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from archived import store  # noqa: E402

LOG_FILE = os.path.expanduser("~/.archived/hook.log")
STATE_FILE = os.path.expanduser("~/.archived/recall_seen.json")
MIN_PROMPT_CHARS = 15  # "yes", "ok" etc. are never worth a search
LIMIT = 3
MAX_SESSIONS = 50  # cap state-file growth; oldest sessions are dropped
HINT = "[archived] call get_memory(id) for details on any memory above."


@contextlib.contextmanager
def _locked(state_path):
    """Hold an exclusive cross-process lock while updating the state file.

    Uses fcntl where available (macOS/Linux) so two hooks firing at once
    can't clobber each other's read-modify-write. On platforms without
    fcntl (e.g. Windows) it is a no-op — the atomic replace still keeps
    the file from being corrupted.
    """
    try:
        import fcntl
    except ImportError:
        yield
        return
    with open(state_path + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _log(msg):
    """Append one line to the hook debug log."""
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(msg.rstrip() + "\n")


def _seen_ids(state_path, session_id):
    """Read the ids already injected in this session (empty set if none)."""
    try:
        with open(state_path) as f:
            return set(json.load(f).get(session_id, []))
    except Exception:
        return set()


def _mark_seen(state_path, session_id, ids):
    """Add ids to this session's injected list, bounded and race-safe.

    Locks the file for the whole read-modify-write, re-inserts the current
    session as most-recent, keeps only the newest MAX_SESSIONS sessions so
    the file can't grow forever, and replaces it atomically.
    """
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with _locked(state_path):
        try:
            with open(state_path) as f:
                state = json.load(f)
        except Exception:
            state = {}
        # pop + re-add moves this session to the end (most-recent) so an
        # active session is never the one pruned below.
        state[session_id] = state.pop(session_id, []) + ids
        for old in list(state)[:-MAX_SESSIONS]:
            del state[old]
        tmp = state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, state_path)


def recall(payload, state_path=STATE_FILE):
    """Build the lines to inject for one prompt; empty list means stay silent."""
    prompt = (payload.get("prompt") or "").strip()
    if len(prompt) < MIN_PROMPT_CHARS:
        return []
    project = os.path.basename(payload.get("cwd") or os.getcwd())
    conn = store.connect()
    try:
        hits = store.search(conn, prompt, {"project": project, "limit": LIMIT})
    finally:
        conn.close()
    session_id = payload.get("session_id", "")
    seen = _seen_ids(state_path, session_id)
    hits = [h for h in hits if h["id"] not in seen]
    if not hits:
        return []
    _mark_seen(state_path, session_id, [h["id"] for h in hits])
    lines = [f"#{h['id']} [{h['type']}] {h['headline']} — {h['day']}" for h in hits]
    lines.append(HINT)
    return lines


def main():
    """Print recalled memories for the prompt being submitted."""
    try:
        payload = json.load(sys.stdin)
        for line in recall(payload):
            print(line)
    except Exception as exc:  # never disturb the user on hook failure
        try:
            _log(f"recall error: {exc}")
        except Exception:
            pass


if __name__ == "__main__":
    main()
