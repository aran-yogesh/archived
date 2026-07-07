#!/usr/bin/env python3
"""SessionStart hook: inject the project's hot context into the new session.

Reads the hook payload from stdin, looks up the hot slot for the current
directory's project, and prints it — stdout from a SessionStart hook is
added to Claude's context. Costs ~150 tokens, once per session.
"""

import json
import os
import sys

# The plugin lives inside the archived repo; make the package importable.
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from archived import store  # noqa: E402


def main():
    """Print hot context for the session's project, if any."""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    project = os.path.basename(payload.get("cwd") or os.getcwd())
    conn = store.connect()
    try:
        hot = store.get_hot(conn, project)
        if hot:
            print(f"[archived] Where you left off in {project}:\n{hot}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
