"""End-to-end tests for the CLI against a real temp database."""

import json
import os
import subprocess
import sys

import pytest

CAPTURE = {
    "project": "churn",
    "log": {"headline": "fixed leakage, retrained XGBoost 0.82->0.87"},
    "facts": [{"headline": "churn: XGBoost beats logreg after leakage fix",
               "tags": ["churn", "modeling"]}],
    "hot": "left off: class imbalance next (SMOTE vs weights)",
}


@pytest.fixture
def env(tmp_path):
    """Environment pointing archived at a temp database file."""
    e = os.environ.copy()
    e["ARCHIVED_DB"] = str(tmp_path / "test.db")
    return e


def run(args, env, stdin=""):
    """Run the CLI and return its stdout."""
    out = subprocess.run([sys.executable, "-m", "archived.cli", *args],
                         input=stdin, capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_ingest_then_read_back(env):
    out = run(["ingest"], env, stdin=json.dumps(CAPTURE))
    assert "log #" in out and "fact #" in out and "hot" in out

    assert "class imbalance" in run(["hot", "churn"], env)
    assert "XGBoost" in run(["search", "xgboost"], env)
    assert "fixed leakage" in run(["recent"], env)


def test_ingest_skips_duplicate_facts(env):
    run(["ingest"], env, stdin=json.dumps(CAPTURE))
    out = run(["ingest"], env, stdin=json.dumps(
        {"project": "churn", "facts": CAPTURE["facts"]}))
    assert "fact #" not in out  # near-duplicate NOOPed

def test_hot_empty_project_prints_nothing(env):
    assert run(["hot", "nothing-here"], env) == ""


def test_backfill_reports_count(env):
    assert run(["backfill"], env).strip() == "embedded 0 memories"
