"""Tests for the per-prompt recall hook (UserPromptSubmit)."""

import importlib.util
import io
import json
import os
import subprocess
import sys

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


def test_prompt_length_boundary(db, state, monkeypatch):
    calls = []
    real = recall_hook.store.search
    monkeypatch.setattr(recall_hook.store, "search",
                        lambda *a: calls.append(1) or real(*a))
    recall_hook.recall(_payload("churn model 14"), state)   # 14 chars -> skipped
    assert calls == []
    recall_hook.recall(_payload("churn model xgb"), state)  # 15 chars -> searched
    assert calls == [1]


def test_other_project_memories_stay_out(db, state):
    conn = store.connect()
    store.save(conn, {"headline": "web app churn XGBoost secret", "project": "web"})
    conn.close()
    lines = recall_hook.recall(_payload("how is the churn XGBoost model tuned"), state)
    assert lines and not any("web app" in line for line in lines)


def test_corrupt_state_file_is_ignored(db, state):
    with open(state, "w") as f:
        f.write("{broken json")
    lines = recall_hook.recall(_payload("how is the churn XGBoost model tuned"), state)
    assert len(lines) == 4  # recall still works, state gets rewritten
    with open(state) as f:
        assert json.load(f)["s1"]


def test_missing_session_id_still_works(db, state):
    payload = {"prompt": "how is the churn XGBoost model tuned",
               "cwd": "/nowhere/demo"}
    assert recall_hook.recall(payload, state)


def test_search_error_stays_silent_and_logs(db, state, tmp_path, monkeypatch, capsys):
    """A store.search failure must print nothing and log the error."""
    log = tmp_path / "hook.log"
    monkeypatch.setattr(recall_hook, "LOG_FILE", str(log))
    monkeypatch.setattr(recall_hook, "STATE_FILE", state)

    def boom(*args):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(recall_hook.store, "search", boom)
    monkeypatch.setattr(
        sys, "stdin",
        io.StringIO(json.dumps(_payload("how is the churn XGBoost model tuned"))),
    )
    recall_hook.main()
    assert capsys.readouterr().out == ""       # silent: 0 tokens
    assert "db exploded" in log.read_text()    # error logged for debugging


# --- end to end: run the script exactly like Claude Code does ----------------

def _run_hook(stdin_text, env):
    """Run recall.py as a subprocess with the given stdin text."""
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "plugin", "hooks", "recall.py")],
        input=stdin_text, capture_output=True, text=True, env=env, timeout=30,
    )


@pytest.fixture
def env(db, tmp_path):
    """Isolated env: temp DB plus temp HOME so state and log stay in tmp."""
    e = os.environ.copy()
    e["HOME"] = str(tmp_path)
    return e


def test_hook_prints_hits_end_to_end(env):
    out = _run_hook(json.dumps(_payload("how is the churn XGBoost model tuned")), env)
    assert out.returncode == 0, out.stderr
    lines = out.stdout.strip().splitlines()
    assert len(lines) == 4
    assert "get_memory" in lines[-1]


def test_hook_silent_on_no_match_end_to_end(env):
    out = _run_hook(json.dumps(_payload("quantum entanglement basics please")), env)
    assert out.returncode == 0, out.stderr
    assert out.stdout == ""  # a miss must cost 0 tokens


def test_hook_survives_garbage_stdin(env):
    out = _run_hook("not json at all", env)
    assert out.returncode == 0
    assert out.stdout == ""
    log = os.path.join(env["HOME"], ".archived", "hook.log")
    assert os.path.exists(log)  # failure logged, user undisturbed
