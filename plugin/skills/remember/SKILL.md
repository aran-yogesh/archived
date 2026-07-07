---
name: remember
description: Use the archived memory tools well — when to save facts, when to search past memories, and the compact format that keeps memory token-lean. Applies whenever the user states a durable preference or decision, references past work ("like last time", "what was I doing", "we decided"), or asks to remember something.
---

# archived: capture & recall discipline

You have four memory tools: `save_memory`, `search_memory`, `get_memory`,
`recent_memories`. A hook already auto-saves a session summary at the end —
your job mid-session is only the *notable* stuff.

## When to search

- User references the past: "like last time", "we decided", "what was I
  doing", "how did I set up X before" → `search_memory` first, don't guess.
- Resuming or planning work in a known project → `recent_memories(project=...)`.
- Search results are headlines. Only call `get_memory(id)` if the headline
  isn't enough to answer.

## When to save (sparingly — the hook catches the rest)

Save a `fact` when something durable surfaces mid-session:
- A decision with a reason ("switched to JWT — sessions don't fit the API")
- A stated preference ("always use uv, not pip")
- A hard-won learning ("X fails on Y unless Z")

Do NOT save:
- Anything derivable from the code, git history, or docs
- One-off session mechanics or partial states (the end-of-session hook logs those)
- Anything you wouldn't plausibly need in a future session

## Format (this is what keeps retrieval cheap)

- `headline`: telegraphic, ≤15 words, keyword-rich — it IS the search result
  ("churn-project: XGBoost over logreg, leakage fixed" — not prose)
- `body`: only if the headline can't carry the detail; ≤100 tokens
- `tags`: 1–4 lowercase topical tags; include the project name
- `meta.project`: the repo/folder name

## The dedup loop

If `save_memory` returns "NOT SAVED — similar memories exist", decide:
- Same info → do nothing (duplicate)
- Newer version of an old memory → retry with `meta.replace_id=<id>`
- Genuinely different → retry with `meta.force=true`

If the user says something that contradicts a memory you retrieved, update
it: save the correction with `replace_id` pointing at the outdated one.
