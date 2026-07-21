"""Tests for user-tunable retrieval settings and the `config` command."""

import json
import os
import subprocess
import sys

import pytest

from archived import config, store


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Point config at a temp file and clear the cache between tests."""
    path = str(tmp_path / "config.json")
    monkeypatch.setenv("ARCHIVED_CONFIG", path)
    config._cache.clear()
    yield path
    config._cache.clear()


def test_defaults_when_no_file(cfg):
    assert config.get("min_cosine") == 0.6
    assert config.get("search_limit") == 5


def test_set_and_read_back(cfg):
    assert config.set_value("min_cosine", "0.75") == 0.75
    assert config.get("min_cosine") == 0.75
    assert json.load(open(cfg))["min_cosine"] == 0.75


def test_search_limit_coerced_to_int(cfg):
    assert config.set_value("search_limit", "3") == 3


def test_unknown_key_rejected(cfg):
    with pytest.raises(KeyError):
        config.set_value("nonsense", "1")


def test_out_of_range_cosine_rejected(cfg):
    with pytest.raises(ValueError):
        config.set_value("min_cosine", "1.5")


def test_bad_search_limit_rejected(cfg):
    with pytest.raises(ValueError):
        config.set_value("search_limit", "0")


def test_search_limit_setting_changes_results(cfg):
    conn = store.connect(":memory:")
    for i in range(6):
        store.save(conn, {"headline": f"redis cache note number {i}"})
    config.set_value("search_limit", "2")
    assert len(store.search(conn, "redis cache note")) == 2
    conn.close()


# --- CLI surface -------------------------------------------------------------

def _cli(args, env):
    """Run the config CLI and return (returncode, stdout, stderr)."""
    out = subprocess.run([sys.executable, "-m", "archived.cli", *args],
                         capture_output=True, text=True, env=env)
    return out.returncode, out.stdout, out.stderr


@pytest.fixture
def env(tmp_path):
    """Isolated config + db for CLI subprocesses."""
    e = os.environ.copy()
    e["ARCHIVED_CONFIG"] = str(tmp_path / "config.json")
    e["ARCHIVED_DB"] = str(tmp_path / "test.db")
    return e


def test_cli_lists_settings(env):
    code, out, _ = _cli(["config"], env)
    assert code == 0
    assert "min_cosine = 0.6" in out and "search_limit = 5" in out


def test_cli_sets_a_value(env):
    code, out, _ = _cli(["config", "search_limit", "8"], env)
    assert code == 0 and "search_limit = 8" in out
    assert _cli(["config", "search_limit"], env)[1].strip() == "8"


def test_cli_rejects_unknown_key(env):
    code, _, err = _cli(["config", "bogus", "1"], env)
    assert code != 0 and "unknown setting" in err
