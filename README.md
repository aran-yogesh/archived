# archived

A personal, portable memory bucket for AI coding tools. One local MCP server;
Claude Code, Codex, and anything MCP-capable plug into the same memory.

Token-lean by design: memories are stored as compact headline+body facts,
search returns headlines only (~15 tokens/hit), and details load on demand.
A session with no memory need costs 0 tokens.

## How it works

```
                     ┌──────────────────────────────┐
   Claude Code ────► │  archived MCP server        │
   (plugin: skill +  │  save / search / get /       │
    auto-capture     │  recent                      │
    hooks)           │                              │
   Codex ──────────► │  SQLite + FTS5               │
   (config.toml)     │  ~/.archived/archived.db   │
                     └──────────────────────────────┘
```

Three memory types:
- **fact** — durable knowledge (decisions, preferences, learnings). Deduped
  at write via an ADD/UPDATE/NOOP loop; never decays.
- **log** — auto-captured session diary ("what did I do Tuesday?"). Recency
  ranked, fades after ~30 days.
- **hot** — one slot per project: current state + next step, injected
  automatically at session start (~150 tokens).

## Install

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
cd archived && uv sync && uv run pytest   # all tests should pass
```

Optional semantic search (hybrid BM25 + embedding retrieval, merged with
reciprocal rank fusion): install the `semantic` extra, then embed any
memories saved before it:

```bash
uv sync --extra semantic     # pulls fastembed (BAAI/bge-small-en-v1.5)
uv run archived backfill     # embed existing memories
```

Without the extra, everything silently stays keyword-only.

### Claude Code (full experience: tools + skill + auto-capture)

```
/plugin marketplace add /Users/aran/Desktop/archived
/plugin install archived@archived-dev
```

The plugin adds: the 4 MCP tools, a `remember` skill (capture discipline),
a SessionStart hook (injects hot context), and a SessionEnd hook
(summarizes the session via a small model call and saves log/facts/hot).

### Codex (tools only — agent-driven capture)

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.archived]
command = "uv"
args = ["run", "--directory", "/Users/aran/Desktop/archived", "archived-server"]
```

### Any other MCP client

Same command: `uv run --directory /path/to/archived archived-server` (stdio).

## CLI

```bash
uv run archived search xgboost     # search from the terminal
uv run archived recent             # session diary
uv run archived hot my-project     # show a hot slot
uv run archived backfill           # embed memories missing embeddings
```

Debug log for the capture hook: `~/.archived/hook.log`.

## Roadmap

- [x] Semantic search: hybrid BM25 + embeddings via the `semantic` extra
- [ ] Codex auto-capture when Codex ships lifecycle hooks
- [ ] Sync/multi-device (the DB is one file — trivially syncable)
- [ ] Web dashboard for browsing/editing memories
