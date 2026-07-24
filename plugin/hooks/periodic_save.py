#!/usr/bin/env python3
"""Stop hook: periodic mid-session save so a long session isn't lost.

The SessionEnd hook only captures on a clean exit — a session that runs
for hours and then crashes (or is killed) would lose everything. This Stop
hook fires after each assistant turn, counts human messages, and every
SAVE_INTERVAL of them backgrounds a capture. It never blocks and never
interrupts the agent — archived captures silently, in the background.

Per-session counters live in a small state file so each session only saves
on its own new messages. All failures are silent — a broken hook must never
disturb the user — but get logged for debugging.
"""

import contextlib
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import capture_bg  # noqa: E402

from archived import mine  # noqa: E402

LOG_FILE = os.path.expanduser("~/.archived/hook.log")
STATE_FILE = os.path.expanduser("~/.archived/save_state.json")
SAVE_INTERVAL = 15   # background a capture every N human messages
MAX_SESSIONS = 50    # cap state-file growth; oldest sessions are dropped


@contextlib.contextmanager
def _locked(state_path):
    """Hold an exclusive cross-process lock while updating the state file.

    Two sessions' Stop hooks can fire at once; the lock keeps their
    read-modify-write of the shared file from clobbering each other. On
    platforms without fcntl it is a no-op — the atomic replace still keeps
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
    with open(LOG_FILE, "a") as handle:
        handle.write(msg.rstrip() + "\n")


def _last_saved(session_id):
    """Human-message count at this session's last save (0 if never)."""
    try:
        with open(STATE_FILE) as handle:
            return json.load(handle).get(session_id, 0)
    except Exception:
        return 0


def _set_saved(session_id, count):
    """Record this session's save point, bounded and race-safe."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with _locked(STATE_FILE):
        try:
            with open(STATE_FILE) as handle:
                state = json.load(handle)
        except Exception:
            state = {}
        # pop + re-add moves this session to most-recent so an active
        # session is never the one pruned below.
        state.pop(session_id, None)
        state[session_id] = count
        for old in list(state)[:-MAX_SESSIONS]:
            del state[old]
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as handle:
            json.dump(state, handle)
        os.replace(tmp, STATE_FILE)


def main():
    """Background a capture once this session crosses the next interval."""
    try:
        payload = json.load(sys.stdin)
        path = payload.get("transcript_path", "")
        if not path or not os.path.exists(path):
            return
        session_id = payload.get("session_id", "")
        count = mine.count_human_messages(path)
        if count - _last_saved(session_id) < SAVE_INTERVAL:
            return
        _set_saved(session_id, count)
        project = os.path.basename(payload.get("cwd") or os.getcwd())
        capture_bg.spawn(path, project)
    except Exception as exc:  # never disturb the user on hook failure
        try:
            _log(f"periodic save error: {exc}")
        except Exception:
            pass


if __name__ == "__main__":
    main()
