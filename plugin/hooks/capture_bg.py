"""Spawn a detached capture so a mid-session hook never blocks the user.

The session-end hook can afford to summarize in the foreground — the
session is already over. The Stop and PreCompact hooks fire while the user
is working, and summarizing calls `claude -p` (seconds). So instead of
running capture inline, they hand the transcript to a detached child that
does the summarize + save on its own and exits. The hook returns instantly.
"""

import os
import subprocess
import sys

# plugin/hooks/capture_bg.py -> repo root holds the archived package.
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def spawn(transcript_path, project):
    """Launch a detached `archived capture` for one transcript, then return.

    Runs the CLI via `-m archived.cli` with the repo on PYTHONPATH, so it
    works without archived being installed on PATH — the same way the other
    hooks locate the package.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    subprocess.Popen(
        [sys.executable, "-m", "archived.cli", "capture",
         transcript_path, "--project", project],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True, env=env)
