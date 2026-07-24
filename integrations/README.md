# Integrations

archived is one local MCP server plus a SQLite store. Any tool that speaks
MCP gets the four memory tools (`save_memory`, `search_memory`,
`get_memory`, `recent_memories`); tools with session hooks can also inject
hot context at start and auto-capture at end.

| Tool | Tools (MCP) | Hot inject | Auto-capture |
|------|:-----------:|:----------:|:------------:|
| Claude Code | ✅ (plugin) | ✅ plugin hook | ✅ plugin hook |
| Codex | ✅ [`codex/config.toml`](codex/config.toml) | — | agent-driven |
| Cursor | ✅ [`cursor/mcp.json`](cursor/mcp.json) | shell hook | shell hook |
| Gemini CLI | ✅ [`gemini/settings.json`](gemini/settings.json) | shell hook | shell hook |
| Anything else | ✅ (point MCP config at `archived-server`) | shell hook | shell hook |

Edit the `--directory` path in each config to wherever you cloned archived.
Prefer `uv tool install archived` and then use the bare `archived-server` /
`archived` commands instead of `uv run`.

## Shell hooks (tool-agnostic)

The scripts in [`shell/`](shell/) cover the two things a bare MCP config
can't do on its own. They only call the `archived` CLI, so they work with
any tool that exposes shell hook points:

- **`archived-session-start.sh`** — prints the current project's hot slot
  (wire into a "session start" / "before first prompt" hook).
- **`archived-session-end.sh <transcript>`** — summarizes a finished
  session's transcript into memories (needs the `claude` CLI for the
  summarization step).
- **`archived-save.sh <headline> [body]`** — save one fact by hand.

Set `ARCHIVED_CMD` if `archived` isn't on your PATH, e.g.
`ARCHIVED_CMD="uv run --directory /path/to/archived archived"`.

## Backfilling history

New install against an existing tool? Don't start empty:

```bash
archived mine                       # mine past Claude Code transcripts
archived mine --since 30 --limit 20 # or bound it by age / count
archived mine-project ~/code/myapp  # seed facts from a project's manifests + git
```

Then keep the store tidy with `archived doctor` and `archived dedup --apply`.
