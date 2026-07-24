#!/bin/sh
# archived — manual save helper (generic, tool-agnostic).
#
# Save one fact from the shell or a tool keybinding:
#   archived-save.sh "prefers uv over pip" "for all Python package installs"
# The first argument is the headline; the optional second is the body.
# Near-duplicate facts are skipped unless you pass --force through ARCHIVED_ARGS.
#
#   ARCHIVED_CMD   override if archived isn't on PATH (see session-start hook)
#   ARCHIVED_ARGS  extra flags, e.g. "--tag preference --project myapp --force"
ARCHIVED_CMD="${ARCHIVED_CMD:-archived}"

[ -z "$1" ] && { echo "usage: archived-save.sh <headline> [body]"; exit 1; }
$ARCHIVED_CMD save "$1" "${2:-}" --project "$(basename "$(pwd)")" $ARCHIVED_ARGS
