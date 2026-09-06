"""Opt-in real CLI test; normal pytest runs remain offline.

Run with --codex-binary codex (or an absolute native executable path).
The test creates its own Git repository and never changes the developer's branch.
"""

import re
import shutil
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from conftest import git
from harness.adapters.codex_cli import CodexCliAdapter
from harness.config import load
from harness.dag import Dag, load_tasks
from harness.events import EventType
from harness.exec.runner import run_dag
from harness.exec.workspace import integration_branch
from harness.git import git as run_git
from harness.models import Verdict


def test_codex_edits_worktree_reads_agents_and_writes_outbox(request, repo):
    binary = request.config.getoption("--codex-binary")
    if not binary:
        pytest.skip("real Codex test requires --codex-binary and an authenticated CLI")

    # pytest's private temp directories use mode 0700. On Windows/Python 3.13+
    # that ACL prevents the native Codex sandbox account from using the cwd and
    # can make newly created outbox files unreadable to the harness. Inherit the
    # normal system-temp ACL instead; retain the run for transcript inspection.
    smoke_root = Path(tempfile.gettempdir()) / f"harness-codex-smoke-{uuid4().hex}"
    smoke_root.mkdir()
    repo = Path(shutil.copytree(repo, smoke_root / "repo"))
    (repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text(
        "# Smoke test instructions\n"
        "Do not delegate. Only edit calculator.py.\n"
        "The double function must have the exact docstring: Harness Codex smoke.\n"
        "Write the requested result.json and handoff.json to the external outbox.\n",
        encoding="utf-8",
    )
    (repo / "calculator.py").write_text(
        "def double(value):\n    raise NotImplementedError\n", encoding="utf-8"
    )
    (repo / "verify_double.py").write_text(
        "from calculator import double\n"
        "assert [double(n) for n in (-4, 0, 7)] == [-8, 0, 14]\n"
        "assert double.__doc__ == 'Harness Codex smoke.'\n",
        encoding="utf-8",
    )
    config_data = {
        "version": 1,
        "defaults": {"adapter": "codex", "profile": "worktree", "max_parallel": 1},
        "adapters": {"codex": {
            "type": "codex_cli", "binary": binary,
            "extra_args": ["--sandbox", "workspace-write", "--ephemeral"],
        }},
        "agent_timeout_s": 180,
        "max_attempts": 1,
        "max_handoff_repairs": 0,
        "command_policy": {
            "default": "require_approval",
            "rules": [{
                "match": "^" + re.escape(f"{sys.executable} verify_double.py") + "$",
                "verdict": "allow",
            }],
        },
    }
    (repo / ".harness" / "config.yaml").write_text(
        yaml.safe_dump(config_data), encoding="utf-8"
    )
    task = {
        "id": "T-001",
        "name": "Implement double(value) in calculator.py to return twice the integer input. Read AGENTS.md.",
        "kind": "implementation",
        "risk": "low",
        "allowed_paths": ["calculator.py"],
        "acceptance": [{
            "cmd": [sys.executable, "verify_double.py"], "expect_fail_before": True,
        }],
    }
    (repo / "tasks").mkdir()
    (repo / "tasks" / "T-001.task.yaml").write_text(yaml.safe_dump(task), encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed Codex smoke test")
    config = load(repo)
    adapter = CodexCliAdapter("codex", config.adapters["codex"].options)
    preflight = adapter.preflight()
    assert preflight.ok, preflight.detail
    scratch = repo.parent / "codex-smoke-scratch"
    store = run_dag(
        repo, config, Dag(load_tasks(repo)), adapter=adapter,
        run_id="codex-smoke", scratch=scratch,
    )
    assert store.state.tasks["T-001"].verdict is Verdict.VERIFIED, (
        f"Codex did not produce verified changes; inspect {store.run_dir} and {scratch}"
    )
    changed = run_git(
        ["diff", "--name-only", "HEAD", integration_branch("codex-smoke")], cwd=repo
    )
    assert changed.exit_code == 0
    assert changed.stdout.strip() == "calculator.py"
    event_types = {event.type for event in store.journal.read()}
    assert EventType.CLAIM_RECEIVED in event_types
    assert EventType.HANDOFF_RECEIVED in event_types
    print(f"Codex smoke verified; evidence: {store.run_dir}")
