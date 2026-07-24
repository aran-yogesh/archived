#!/bin/sh
# archived — session-start hook (generic, tool-agnostic).
#
# Prints the current project's hot slot so a tool can inject it into a new
# session's context. Wire this into whatever "session start" / "before
# prompt" hook your tool provides. The project is the current directory's
# basename, matching how the Claude Code plugin scopes memories.
#
# Override the CLI if archived isn't on PATH as `archived`:
#   ARCHIVED_CMD="uv run --directory /path/to/archived archived"
ARCHIVED_CMD="${ARCHIVED_CMD:-archived}"

project="$(basename "$(pwd)")"
hot="$($ARCHIVED_CMD hot "$project" 2>/dev/null)"
[ -n "$hot" ] && printf '[archived] Where you left off in %s:\n%s\n' "$project" "$hot"
exit 0
