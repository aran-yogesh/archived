"""Tests for mining past transcripts and project dirs into memories."""

import json

import pytest

from archived import mine, store


@pytest.fixture
def conn():
    """Fresh in-memory database for each test."""
    c = store.connect(":memory:")
    yield c
    c.close()


def _write_transcript(path):
    """Write a JSONL transcript with enough text to be non-trivial."""
    filler = "worked on the retry logic and fixed the flaky timeout. " * 12
    lines = [
        {"type": "user", "message": {"content": "help me fix the retry bug"}},
        {"type": "assistant", "message": {"content": filler}},
    ]
    path.write_text("\n".join(json.dumps(entry) for entry in lines))


def test_read_transcript_extracts_text(tmp_path):
    path = tmp_path / "s.jsonl"
    _write_transcript(path)
    text = mine.read_transcript(str(path))
    assert "retry bug" in text
    assert "flaky timeout" in text


def test_read_transcript_ignores_non_message_lines(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(
        json.dumps({"type": "summary", "message": {"content": "meta"}}) + "\n"
        + json.dumps({"type": "user", "message": {"content": "real prompt"}}))
    text = mine.read_transcript(str(path))
    assert "real prompt" in text
    assert "meta" not in text


def test_project_of_recovers_label():
    # Claude Code flattens the cwd into the slug; last segment is the project
    assert mine._project_of("/home/-Users-me-code-churn/abc.jsonl") == "churn"


def test_mine_transcripts_ingests_and_is_idempotent(conn, tmp_path, monkeypatch):
    _write_transcript(tmp_path / "a.jsonl")
    monkeypatch.setattr(mine, "TRANSCRIPT_ROOT", str(tmp_path))
    monkeypatch.setattr(mine, "summarize", lambda _t: {
        "log": {"headline": "fixed the retry bug", "body": "raised timeout"},
        "facts": [{"headline": "retries use exponential backoff",
                   "tags": ["retry"]}]})

    first = mine.mine_transcripts(conn)
    assert len(first) == 1 and "saved" in first[0][1]
    assert store.recent(conn)[0]["headline"] == "fixed the retry bug"

    # second run skips the already-mined, unchanged transcript
    assert mine.mine_transcripts(conn) == []


def test_mine_transcripts_dry_run_saves_nothing(conn, tmp_path, monkeypatch):
    _write_transcript(tmp_path / "a.jsonl")
    monkeypatch.setattr(mine, "TRANSCRIPT_ROOT", str(tmp_path))
    monkeypatch.setattr(mine, "summarize", lambda _t: {
        "log": {"headline": "did a thing", "body": ""}, "facts": []})

    results = mine.mine_transcripts(conn, {"dry_run": True})
    assert results[0][1] == "dry-run"
    assert store.recent(conn) == []
    # dry run doesn't mark the source, so a real run still picks it up
    assert mine.mine_transcripts(conn)[0][1] != "dry-run"


def test_mine_transcripts_skips_trivial(conn, tmp_path, monkeypatch):
    (tmp_path / "tiny.jsonl").write_text(
        json.dumps({"type": "user", "message": {"content": "hi"}}))
    monkeypatch.setattr(mine, "TRANSCRIPT_ROOT", str(tmp_path))
    results = mine.mine_transcripts(conn)
    assert results[0][1] == "trivial"
    assert store.recent(conn) == []


def test_mine_project_seeds_language_fact(conn, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    out = mine.mine_project(conn, str(tmp_path))
    assert "saved: fact" in out
    hits = store.search(conn, "python project")
    assert any("Python project" in h["headline"] for h in hits)


def test_mine_project_reports_nothing_for_bare_dir(conn, tmp_path):
    assert mine.mine_project(conn, str(tmp_path)) == "saved: nothing new"


def test_capture_transcript_saves_log_facts_and_hot(conn, tmp_path, monkeypatch):
    _write_transcript(tmp_path / "s.jsonl")
    monkeypatch.setattr(mine, "summarize", lambda _t, _p=None: {
        "log": {"headline": "fixed retries", "body": "raised timeout"},
        "facts": [{"headline": "retries use backoff", "tags": ["retry"]}],
        "hot": "next: add jitter to the backoff"})
    status = mine.capture_transcript(conn, str(tmp_path / "s.jsonl"), "demo")
    assert "log" in status and "fact" in status and "hot" in status
    assert store.get_hot(conn, "demo") == "next: add jitter to the backoff"


def test_capture_transcript_skips_trivial(conn, tmp_path):
    (tmp_path / "s.jsonl").write_text(
        json.dumps({"type": "user", "message": {"content": "hi"}}))
    assert mine.capture_transcript(conn, str(tmp_path / "s.jsonl")) == "trivial"


def test_count_human_messages_ignores_tool_results(tmp_path):
    lines = [
        {"type": "user", "message": {"content": "first question"}},
        {"type": "assistant", "message": {"content": "answer"}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": "bash output"}]}},
        {"type": "user", "message": {"content": [{"type": "text",
                                                  "text": "second question"}]}},
    ]
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(entry) for entry in lines))
    assert mine.count_human_messages(str(path)) == 2
