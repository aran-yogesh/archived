"""Deployment checklist: everything a fresh install needs must actually work.

Each test is one item on the checklist:
  1. plugin/marketplace/MCP/hooks manifests are valid and consistent
  2. the skill has proper frontmatter
  3. hook scripts run standalone without crashing
  4. package entry points import
  5. the MCP server boots over stdio and serves all 4 tools
"""

import json
import os
import subprocess
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "plugin")


def _read_json(*path):
    """Load a JSON file under the repo root."""
    with open(os.path.join(ROOT, *path)) as f:
        return json.load(f)


# --- 1. manifests -----------------------------------------------------------

def test_plugin_manifest_valid():
    manifest = _read_json("plugin", ".claude-plugin", "plugin.json")
    assert manifest["name"] == "archived"
    assert manifest["version"]
    assert manifest["description"]


def test_marketplace_points_at_plugin():
    market = _read_json(".claude-plugin", "marketplace.json")
    entry = market["plugins"][0]
    assert entry["name"] == "archived"
    source = os.path.join(ROOT, entry["source"])
    assert os.path.isdir(source), f"marketplace source missing: {source}"
    assert os.path.isfile(os.path.join(source, ".claude-plugin", "plugin.json"))


def test_mcp_config_launches_from_plugin_root():
    mcp = _read_json("plugin", ".mcp.json")
    server = mcp["mcpServers"]["archived"]
    assert server["command"] == "uv"
    # the --directory arg must resolve to the repo (where pyproject lives)
    directory = [a for a in server["args"] if "CLAUDE_PLUGIN_ROOT" in a][0]
    resolved = directory.replace("${CLAUDE_PLUGIN_ROOT}", PLUGIN)
    assert os.path.isfile(os.path.join(resolved, "pyproject.toml"))


def test_hooks_config_references_existing_scripts():
    hooks = _read_json("plugin", "hooks", "hooks.json")["hooks"]
    assert set(hooks) == {"SessionStart", "UserPromptSubmit", "SessionEnd"}
    for event, groups in hooks.items():
        for group in groups:
            for hook in group["hooks"]:
                script = hook["command"].split('"')[1]
                path = script.replace("${CLAUDE_PLUGIN_ROOT}", PLUGIN)
                assert os.path.isfile(path), f"{event} script missing: {path}"


# --- 2. skill ----------------------------------------------------------------

def test_skill_has_frontmatter():
    path = os.path.join(PLUGIN, "skills", "remember", "SKILL.md")
    with open(path) as f:
        text = f.read()
    assert text.startswith("---")
    frontmatter = text.split("---")[1]
    assert "name: remember" in frontmatter
    assert "description:" in frontmatter


# --- 3. hook scripts run standalone -----------------------------------------

def _run_hook(script, payload, env):
    """Run a hook script with a JSON payload on stdin; return the result."""
    return subprocess.run(
        [sys.executable, os.path.join(PLUGIN, "hooks", script)],
        input=json.dumps(payload), capture_output=True, text=True,
        env=env, timeout=30,
    )


@pytest.fixture
def env(tmp_path):
    """Environment with an isolated temp database."""
    e = os.environ.copy()
    e["ARCHIVED_DB"] = str(tmp_path / "deploy.db")
    return e


def test_session_start_hook_runs_clean(env):
    out = _run_hook("session_start.py", {"cwd": "/nowhere/special"}, env)
    assert out.returncode == 0, out.stderr
    assert out.stdout == ""  # no hot slot -> inject nothing


def test_session_start_hook_injects_hot(env, tmp_path):
    seed = {"project": "special", "hot": "left off: deploy checklist"}
    subprocess.run([sys.executable, "-m", "archived.cli", "ingest"],
                   input=json.dumps(seed), capture_output=True, text=True,
                   env=env, check=True)
    out = _run_hook("session_start.py", {"cwd": "/nowhere/special"}, env)
    assert "deploy checklist" in out.stdout


def test_capture_hook_survives_bad_input(env):
    # missing transcript, malformed payloads: must exit 0 and stay silent
    for payload in ({}, {"transcript_path": "/does/not/exist"}):
        out = _run_hook("capture.py", payload, env)
        assert out.returncode == 0, out.stderr


# --- 4. entry points ---------------------------------------------------------

def test_entry_points_import():
    from archived.cli import main as cli_main
    from archived.server import main as server_main
    assert callable(cli_main) and callable(server_main)


# --- 5. MCP server boots and serves the tools --------------------------------

def test_mcp_server_stdio_handshake(env):
    """The one test that proves a fresh install actually works end to end."""
    messages = "\n".join(json.dumps(m) for m in [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "deploy-check", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]) + "\n"
    proc = subprocess.Popen(
        [sys.executable, "-m", "archived.server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env,
    )
    # Read stdout in a thread and stop at the tools/list reply. stdin is kept
    # open the whole time so the server never sees EOF and shuts down before
    # flushing the reply — that race made this test flaky on loaded runners.
    tools = []

    def _read_reply():
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == 2:
                tools[:] = [t["name"] for t in msg["result"]["tools"]]
                return

    reader = threading.Thread(target=_read_reply, daemon=True)
    reader.start()
    proc.stdin.write(messages)
    proc.stdin.flush()
    reader.join(timeout=60)
    proc.stdin.close()
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    assert tools == ["save_memory", "search_memory",
                     "get_memory", "recent_memories"]
