#!/usr/bin/env python3
"""SessionEnd hook: summarize the session and save it to archived.

Reads the transcript, asks a small model (via `claude -p`) for a capture
JSON (log + facts + hot slot), and ingests it. All failures are silent —
a broken hook must never disturb the user — but get logged to
~/.archived/hook.log for debugging.
"""

import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from archived import cli, store  # noqa: E402

LOG_FILE = os.path.expanduser("~/.archived/hook.log")
MAX_TRANSCRIPT_CHARS = 12000  # tail is what matters for "where I left off"

PROMPT = """Read this coding session transcript and output ONLY a JSON object:
{"log": {"headline": "<what was done, <=15 words, telegraphic>",
         "body": "<key details, <=60 words>"},
 "facts": [{"headline": "<durable fact worth remembering across sessions>",
            "tags": ["<tag>"]}],
 "hot": "<current state + exact next step, <=50 words>"}

Rules: facts = decisions, preferences, learnings only — NOT things derivable
from the code/git (structure, what a commit did) and NOT one-off mechanics.
Zero to three facts, usually zero or one. If the session was trivial
(greetings, one quick question), output {} instead. No markdown fences."""


def _log(msg):
    """Append one line to the hook debug log."""
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(msg.rstrip() + "\n")


def _transcript_text(path):
    """Pull readable user/assistant text out of a transcript JSONL file."""
    lines = []
    with open(path) as f:
        for raw in f:
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


def _summarize(transcript):
    """Ask a small model for the capture JSON; returns a dict or None."""
    result = subprocess.run(
        ["claude", "-p", PROMPT, "--model", "haiku"],
        input=transcript, capture_output=True, text=True, timeout=90,
    )
    if result.returncode != 0:
        _log(f"claude -p failed: {result.stderr[:200]}")
        return None
    text = result.stdout.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        _log(f"bad capture JSON: {text[:200]}")
        return None


def main():
    """Capture the ending session into archived."""
    try:
        payload = json.load(sys.stdin)
        transcript_path = payload.get("transcript_path", "")
        if not transcript_path or not os.path.exists(transcript_path):
            return
        transcript = _transcript_text(transcript_path)
        if len(transcript) < 400:
            return  # trivial session, nothing worth remembering
        capture = _summarize(transcript)
        if not capture:
            return
        capture["project"] = os.path.basename(payload.get("cwd") or os.getcwd())
        conn = store.connect()
        try:
            _log(f"{capture['project']}: {cli.ingest(conn, capture)}")
        finally:
            conn.close()
    except Exception as exc:  # never disturb the user on hook failure
        try:
            _log(f"capture error: {exc}")
        except Exception:
            pass


if __name__ == "__main__":
    main()
