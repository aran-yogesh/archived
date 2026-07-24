#!/usr/bin/env python3
"""PreCompact hook: save the session before its context is compacted away.

Claude Code fires PreCompact right before it shrinks the conversation to
free up context. Anything not yet in archived would be lost, so this hook
captures the session first. It backgrounds the work (the transcript is
already on disk) so compaction is never delayed. All failures are silent —
a broken hook must never disturb the user — but get logged for debugging.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture_bg  # noqa: E402

LOG_FILE = os.path.expanduser("~/.archived/hook.log")


def _log(msg):
    """Append one line to the hook debug log."""
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a") as handle:
        handle.write(msg.rstrip() + "\n")


def main():
    """Background a capture of the session about to be compacted."""
    try:
        payload = json.load(sys.stdin)
        path = payload.get("transcript_path", "")
        if not path or not os.path.exists(path):
            return
        project = os.path.basename(payload.get("cwd") or os.getcwd())
        capture_bg.spawn(path, project)
    except Exception as exc:  # never disturb the user on hook failure
        try:
            _log(f"precompact error: {exc}")
        except Exception:
            pass


if __name__ == "__main__":
    main()
