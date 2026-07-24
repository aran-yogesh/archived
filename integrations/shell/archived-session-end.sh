#!/bin/sh
# archived — session-end hook (generic, tool-agnostic).
#
# Summarizes one finished session's transcript into memories. Pass the
# transcript file path as $1 (most CLIs expose it as an env var or hook
# argument). Requires the `claude` CLI for the summarization step; if it
# is missing the capture is skipped silently.
#
#   ARCHIVED_CMD  override if archived isn't on PATH (see session-start hook)
ARCHIVED_CMD="${ARCHIVED_CMD:-archived}"

transcript="$1"
[ -z "$transcript" ] && exit 0
[ -f "$transcript" ] || exit 0
$ARCHIVED_CMD capture "$transcript" --project "$(basename "$(pwd)")" >/dev/null 2>&1
exit 0
