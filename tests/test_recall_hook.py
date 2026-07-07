"""Tests for the per-prompt recall hook (UserPromptSubmit)."""

import importlib.util
import os

import pytest

from archived import store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_hook():
    """Import plugin/hooks/recall.py as a module (it is not a package)."""
    path = os.path.join(ROOT, "plugin", "hooks", "recall.py")
    spec = importlib.util.spec_from_file_location("recall_hook", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


recall_hook = _load_hook()


def _payload(prompt, session_id="s1"):
    """Build a hook payload for the demo project."""
    return {"prompt": prompt, "cwd": "/nowhere/demo", "session_id": session_id}


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Point ARCHIVED_DB at a temp database seeded with demo memories."""
    monkeypatch.setenv("ARCHIVED_DB", str(tmp_path / "recall.db"))
    conn = store.connect()
    for i in range(3):
        store.save(conn, {"headline": f"churn model note {i} uses XGBoost",
                          "project": "demo", "tags": ["modeling"]})
    conn.close()


@pytest.fixture
def state(tmp_path):
    """Path for a fresh per-session seen-ids state file."""
    return str(tmp_path / "seen.json")


def test_matching_prompt_injects_headlines_and_hint(db, state):
    lines = recall_hook.recall(_payload("how is the churn XGBoost model tuned"), state)
    assert len(lines) == 4  # limit of 3 hits + one hint line
    for line in lines[:3]:
        assert line.startswith("#")
        assert "[fact]" in line and "XGBoost" in line and "—" in line
    assert "get_memory" in lines[-1]


def test_no_match_injects_nothing(db, state):
    lines = recall_hook.recall(_payload("tell me about quantum entanglement"), state)
    assert lines == []


def test_same_session_never_repeats_ids(db, state):
    first = recall_hook.recall(_payload("how is the churn XGBoost model tuned"), state)
    assert first
    again = recall_hook.recall(_payload("more about the XGBoost churn model"), state)
    assert again == []  # same top hits, already injected -> total silence


def test_other_session_gets_ids_again(db, state):
    first = recall_hook.recall(_payload("how is the churn XGBoost model tuned", "s1"), state)
    other = recall_hook.recall(_payload("how is the churn XGBoost model tuned", "s2"), state)
    assert sorted(other) == sorted(first)  # seen state is per-session


def test_trivial_prompt_skips_search(db, state, monkeypatch):
    def boom(*args):
        raise AssertionError("search must not run for trivial prompts")
    monkeypatch.setattr(recall_hook.store, "search", boom)
    assert recall_hook.recall(_payload("ok"), state) == []
