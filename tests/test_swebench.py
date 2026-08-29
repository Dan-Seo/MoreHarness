"""외부 벤치마크 fixture 컨버터 (M8). 계약은 docs/11 의 「외부 벤치마크」다."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from harness.cli import main
from harness.eval import arms, fixtures
from harness.eval.swebench import convert
from harness.git import git

TEST_PATCH = """\
diff --git a/tests/test_hidden.py b/tests/test_hidden.py
new file mode 100644
--- /dev/null
+++ b/tests/test_hidden.py
@@ -0,0 +1,2 @@
+def test_hidden():
+    assert True
"""


def checkout(root: Path, repo: str) -> str:
    """`--repos` 아래의 로컬 체크아웃 하나. 반환값은 base_commit 이다."""
    path = root / repo.replace("/", "__")
    (path / "src").mkdir(parents=True)
    (path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (path / "README.md").write_text("# app\n", encoding="utf-8")
    git(["init", "-q", "-b", "main"], cwd=path)
    git(["config", "user.email", "t@t.local"], cwd=path)
    git(["config", "user.name", "t"], cwd=path)
    git(["add", "-A"], cwd=path)
    git(["commit", "-q", "-m", "base"], cwd=path)
    return git(["rev-parse", "HEAD"], cwd=path).stdout.strip()


def instance(instance_id: str, repo: str, base_commit: str, **overrides) -> dict:
    data = {
        "instance_id": instance_id,
        "repo": repo,
        "base_commit": base_commit,
        "problem_statement": "app.value 가 2 여야 하는데 1 이다.",
        "test_patch": TEST_PATCH,
        "FAIL_TO_PASS": '["tests/test_hidden.py::test_hidden"]',
        "PASS_TO_PASS": '["tests/test_old.py::test_old"]',
        "version": "1.0",
        "environment_setup_commit": base_commit,
        "created_at": "2024-01-01T00:00:00Z",
    }
    data.update(overrides)
    return data


def instances_file(root: Path, *records: dict) -> Path:
    path = root / "instances.jsonl"
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )
    return path


@pytest.fixture
def converted(tmp_path):
    repos = tmp_path / "repos"
    repos.mkdir()
    sha = checkout(repos, "acme/app")
    jsonl = instances_file(tmp_path, instance("acme__app-1", "acme/app", sha))
    out = tmp_path / "out"
    report = convert(jsonl, repos, out)
    return report, out / "acme__app-1", sha


# --------------------------------------------------------------------------- 변환


def test_the_instance_becomes_a_fixture(converted):
    report, case, sha = converted
    assert report.created == ("acme__app-1",)
    assert report.skipped == ()

    # seed 는 base_commit 의 트리이고 .git 은 따라오지 않는다
    assert (case / "seed" / "src" / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert not (case / "seed" / ".git").exists()

    spec = yaml.safe_load((case / "seed" / "specs" / "acme__app-1" / "spec.yaml").read_text("utf-8"))
    assert spec["requirements"][0]["id"] == "R-001"
    assert "1 이다" in spec["requirements"][0]["statement"]

    meta = yaml.safe_load((case / "meta.yaml").read_text(encoding="utf-8"))
    assert meta["benchmark"] == "swebench"
    assert meta["instance_id"] == "acme__app-1"
    assert meta["base_commit"] == sha


def test_the_fixture_declares_its_own_adapter_and_policy(converted):
    """docs/11 — `.harness/` 는 fixture 가 자기 어댑터와 Command Policy 를 선언하는 자리다."""
    _, case, _ = converted
    config = yaml.safe_load((case / "seed" / ".harness" / "config.yaml").read_text("utf-8"))
    assert config["defaults"]["adapter"] in config["adapters"]
    assert config["command_policy"]["default"] in ("deny", "require_approval")
    # grader 가 테스트 패치를 적용할 수 있어야 한다
    assert any("git apply" in rule["match"] for rule in config["command_policy"]["rules"])
    assert (case / "seed" / ".harness" / "constitution.md").is_file()
    assert (case / "seed" / ".harness" / "approved_commands.yaml").is_file()


def test_the_task_is_left_to_the_harness_scaffold(converted):
    """docs/11 — 컨버터는 `tasks/` 를 만들지 않는다. R-### 하나당 골격 하나는 하네스가 만들고,
    **보이는 AC 는 없다** — 채점 기준을 숨기는 것이 이 벤치마크의 본질이다."""
    from harness.dag import load_tasks

    _, case, _ = converted
    assert not (case / "tasks").exists()

    repo = fixtures.materialize(fixtures.load(case), case.parent / "work")
    tasks = load_tasks(repo)
    assert [task.satisfies for task in tasks.values()] == [("R-001",)]
    assert all(not task.acceptance for task in tasks.values())


# --------------------------------------------------------------------------- hidden


def test_the_hidden_tests_never_reach_the_seed(converted):
    """docs/11 의 절대 규칙 — grader 자료는 트리에도 컨텍스트에도 들어가지 않는다."""
    _, case, _ = converted
    assert (case / "grader" / "test_patch.diff").read_text(encoding="utf-8") == TEST_PATCH
    assert not (case / "seed" / "tests" / "test_hidden.py").exists()
    assert not any(
        "test_hidden" in path.read_text(encoding="utf-8", errors="ignore")
        for path in fixtures.context_sources(fixtures.load(case))
    )


def test_the_grading_criteria_mirror_the_benchmark(converted):
    _, case, _ = converted
    entries = yaml.safe_load((case / "grader" / "hidden_ac.yaml").read_text("utf-8"))["acceptance"]
    assert [entry["cmd"] for entry in entries] == [
        ["git", "apply", "{grader}/test_patch.diff"],
        ["python", "-m", "pytest", "-q", "tests/test_hidden.py::test_hidden"],
        ["python", "-m", "pytest", "-q", "tests/test_old.py::test_old"],
    ]
    # 회귀 채점 기준은 만들지 않는다 — 이 fixture 는 능력 eval 용이다
    assert not (case / "grader" / "expected.yaml").exists()


def test_the_grader_placeholder_resolves_to_the_fixture(converted):
    """`{grader}` 는 grader 실행에서만 절대 경로로 치환된다 (docs/11)."""
    _, case, _ = converted
    first = arms._hidden_acceptance(fixtures.load(case))[0][0]
    assert "{grader}" not in first[2]
    assert Path(first[2]).is_file()


# --------------------------------------------------------------------------- 건너뛰기


def test_a_missing_checkout_skips_only_that_instance(tmp_path):
    repos = tmp_path / "repos"
    repos.mkdir()
    sha = checkout(repos, "acme/app")
    jsonl = instances_file(
        tmp_path,
        instance("acme__app-1", "acme/app", sha),
        instance("ghost__lib-9", "ghost/lib", sha),
    )

    report = convert(jsonl, repos, tmp_path / "out")

    assert report.created == ("acme__app-1",)
    assert [case for case, _ in report.skipped] == ["ghost__lib-9"]
    assert not (tmp_path / "out" / "ghost__lib-9").exists()


def test_an_unknown_base_commit_is_skipped(tmp_path):
    repos = tmp_path / "repos"
    repos.mkdir()
    checkout(repos, "acme/app")
    jsonl = instances_file(tmp_path, instance("acme__app-1", "acme/app", "0" * 40))

    report = convert(jsonl, repos, tmp_path / "out")

    assert report.created == ()
    assert report.skipped[0][0] == "acme__app-1"


def test_an_instance_without_grading_criteria_is_skipped(tmp_path):
    """채점 기준 없는 fixture 는 만들지 않는다 (docs/11)."""
    repos = tmp_path / "repos"
    repos.mkdir()
    sha = checkout(repos, "acme/app")
    jsonl = instances_file(
        tmp_path,
        instance("acme__app-1", "acme/app", sha, FAIL_TO_PASS="[]", PASS_TO_PASS="[]"),
    )

    report = convert(jsonl, repos, tmp_path / "out")

    assert report.created == ()
    assert report.skipped[0][0] == "acme__app-1"


def test_both_checkout_layouts_are_accepted(tmp_path):
    repos = tmp_path / "repos"
    (repos / "acme").mkdir(parents=True)
    sha = checkout(repos / "acme", "app")  # <owner>/<name> 배치
    jsonl = instances_file(tmp_path, instance("acme__app-1", "acme/app", sha))

    report = convert(jsonl, repos, tmp_path / "out")

    assert report.created == ("acme__app-1",)


# --------------------------------------------------------------------------- 멱등


def test_converting_twice_gives_the_same_tree(tmp_path):
    repos = tmp_path / "repos"
    repos.mkdir()
    sha = checkout(repos, "acme/app")
    jsonl = instances_file(tmp_path, instance("acme__app-1", "acme/app", sha))
    out = tmp_path / "out"

    convert(jsonl, repos, out)
    before = _snapshot(out)
    convert(jsonl, repos, out)

    assert _snapshot(out) == before


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)).replace("\\", "/"): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# --------------------------------------------------------------------------- CLI


def test_eval_import_reports_created_and_skipped(tmp_path, capsys):
    repos = tmp_path / "repos"
    repos.mkdir()
    sha = checkout(repos, "acme/app")
    jsonl = instances_file(
        tmp_path,
        instance("acme__app-1", "acme/app", sha),
        instance("ghost__lib-9", "ghost/lib", sha),
    )

    code = main(
        [
            "eval",
            "import",
            "--benchmark",
            "swebench",
            "--instances",
            str(jsonl),
            "--repos",
            str(repos),
            "--out",
            str(tmp_path / "out"),
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "acme__app-1" in out
    assert "ghost__lib-9" in out
    assert (tmp_path / "out" / "acme__app-1" / "grader" / "hidden_ac.yaml").is_file()


def test_an_unknown_benchmark_is_refused(tmp_path, capsys):
    code = main(
        [
            "eval",
            "import",
            "--benchmark",
            "nonesuch",
            "--instances",
            str(tmp_path / "x.jsonl"),
            "--repos",
            str(tmp_path),
            "--out",
            str(tmp_path / "out"),
        ]
    )
    assert code == 2
