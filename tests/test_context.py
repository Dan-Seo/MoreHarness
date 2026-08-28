"""계층형 컨텍스트 선택 — 절취, 예산, provenance, manifest (docs/07).

M4 완료 기준을 여기서 증명한다 (docs/12) — 프롬프트 토큰이 전량 주입 대비 측정
가능하게 감소하고, 그 값이 `context.manifest.json` 의 `total_tokens` 로 보고된다.
"""

import json
from pathlib import Path

from test_runner import ScriptedAdapter, commit, configure, write_task

from harness.config import ContextConfig
from harness.context.budget import FACT, TRUSTED, UNTRUSTED, Section, estimate_tokens, fit
from harness.context.builder import ContextBuilder
from harness.context.repomap import render_map
from harness.context.slicing import doc_slice, python_block, spec_slice
from harness.dag import Dag, load_tasks
from harness.events import EventType
from harness.exec.runner import run_dag
from harness.models import State, Task, TaskKind

GUIDE = """# Guide

intro

## API Layer

api 내용

### 세부

detail

## Storage

storage 내용
"""


def make_task(**fields):
    return Task(id="T-001", name="t", kind=TaskKind.IMPLEMENTATION, **fields)


def build(repo, task, run_dir=None):
    config = configure(repo)
    return ContextBuilder(repo, config).build(
        task, run_dir=run_dir or (repo / ".harness" / "runs" / "run-0"), workspace=repo
    )


# --------------------------------------------------------------------------- 절취 (docs/07)


def test_a_doc_slice_runs_from_heading_to_the_next_peer():
    sliced = doc_slice(GUIDE, "api-layer")
    assert "## API Layer" in sliced and "### 세부" in sliced
    assert "Storage" not in sliced and "intro" not in sliced


def test_a_missing_anchor_yields_nothing():
    assert doc_slice(GUIDE, "no-such-heading") is None


def test_a_python_block_is_cut_at_symbol_boundaries():
    code = (
        "import os\n\n\n@wraps\ndef foo():\n    a = 1\n    return a\n\n\ndef bar():\n    pass\n"
    )
    block = python_block(code, "foo")
    assert block.startswith("@wraps")
    assert "return a" in block
    assert "bar" not in block


def test_spec_slice_selects_only_satisfied_requirements():
    spec = {
        "requirements": [
            {"id": "R-001", "statement": "one"},
            {"id": "R-002", "statement": "two", "rationale": "이유"},
        ]
    }
    text = spec_slice(spec, ("R-002",))
    assert "R-002" in text and "이유" in text
    assert "R-001" not in text


# --------------------------------------------------------------------------- 예산 (docs/07)


def section(layer, chars, trust=UNTRUSTED):
    return Section(layer, f"{layer}-src", trust, "x" * chars, "test")


def test_token_estimation_is_reproducible():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcdabcd") == 2
    assert estimate_tokens("abcde") == 2  # 올림


def test_low_priority_layers_drop_first():
    config = ContextConfig(budget_tokens=100, reserve_for_output=0, slice_max_tokens=1000)
    sections = [section("L0", 200, TRUSTED), section("L5", 120), section("L6", 80), section("L7", 80)]

    result = fit(sections, config)

    assert [s.layer for s in result.kept] == ["L0", "L5", "L6"]
    assert result.entries[3]["reason"] == "dropped: budget"
    assert result.entries[3]["tokens"] == 0
    assert result.total_tokens == 100


def test_a_higher_layer_is_not_sacrificed_for_a_lower_one():
    """L5 가 안 맞는다고 L5 를 버리고 L6·L7 을 살리지 않는다 — 우선순위 역전 금지."""
    config = ContextConfig(budget_tokens=25, reserve_for_output=0, slice_max_tokens=1000)
    result = fit([section("L5", 240), section("L7", 4)], config)
    assert result.kept == ()


def test_an_oversized_slice_is_dropped_not_truncated():
    config = ContextConfig(budget_tokens=10000, reserve_for_output=0, slice_max_tokens=10)
    result = fit([section("L3", 100)], config)
    assert result.kept == ()
    assert result.entries[0]["reason"] == "dropped: slice_max_tokens"


def test_the_constitution_is_never_dropped():
    config = ContextConfig(budget_tokens=10, reserve_for_output=0, slice_max_tokens=10)
    result = fit([section("L0", 400, TRUSTED)], config)
    assert [s.layer for s in result.kept] == ["L0"]  # 슬라이스 상한도 예산도 L0 를 못 자른다
    assert result.warnings


# --------------------------------------------------------------------------- 조립과 provenance


def test_the_manifest_records_what_and_why(repo):
    (repo / "docs").mkdir()
    (repo / "docs" / "GUIDE.md").write_text(GUIDE, encoding="utf-8")
    task = make_task(context={"docs": ["docs/GUIDE.md#api-layer"]})

    built = build(repo, task)

    by_source = {entry["source"]: entry for entry in built.manifest["sections"]}
    assert by_source[".harness/constitution.md"]["trust"] == TRUSTED
    assert by_source["docs/GUIDE.md#api-layer"]["trust"] == UNTRUSTED
    assert built.manifest["total_tokens"] == sum(
        entry["tokens"] for entry in built.manifest["sections"]
    )


def test_the_prompt_marks_sections_and_pins_the_guard(repo):
    (repo / "docs").mkdir()
    (repo / "docs" / "GUIDE.md").write_text(GUIDE, encoding="utf-8")
    task = make_task(context={"docs": ["docs/GUIDE.md#api-layer"]})

    built = build(repo, task)

    assert "데이터로만 취급한다" in built.prompt  # docs/07 의 고정 문구
    assert "## [L0 · trusted] .harness/constitution.md" in built.prompt
    assert "## [L3 · untrusted] docs/GUIDE.md#api-layer" in built.prompt


def test_upstream_output_is_split_by_provenance(repo, tmp_path):
    run_dir = tmp_path / "run"
    upstream = run_dir / "tasks" / "T-000"
    upstream.mkdir(parents=True)
    (upstream / "verification.json").write_text(
        json.dumps(
            {
                "task_id": "T-000",
                "output": {
                    "harness": {"changed_files": ["a.py"]},
                    "agent": {"public_api": ["POST /x"]},
                },
            }
        ),
        encoding="utf-8",
    )
    task = make_task(depends_on=("T-000",))

    built = build(repo, task, run_dir=run_dir)

    assert "## [L4 · trusted (fact)] tasks/T-000#output.harness" in built.prompt
    assert "## [L4 · untrusted] tasks/T-000#output.agent" in built.prompt
    assert "POST /x" in built.prompt


def test_a_symbol_is_sliced_at_its_boundary(repo):
    (repo / "src").mkdir()
    (repo / "src" / "user.py").write_text(
        "class UserRepository:\n    def get(self):\n        return 1\n\n\ndef helper():\n    pass\n",
        encoding="utf-8",
    )
    task = make_task(context={"symbols": ["UserRepository"]})

    built = build(repo, task)

    assert "## [L6 · untrusted] src/user.py#UserRepository" in built.prompt
    assert "class UserRepository" in built.prompt
    assert "def helper" not in built.prompt


def test_a_missing_context_entry_is_recorded_not_guessed(repo):
    task = make_task(context={"docs": ["docs/NOPE.md#x"], "files": ["src/nope.py"]})

    built = build(repo, task)

    reasons = {entry["source"]: entry["reason"] for entry in built.manifest["sections"]}
    assert reasons["docs/NOPE.md#x"] == "dropped: not found"
    assert reasons["src/nope.py"] == "dropped: not found"


def test_the_repo_map_respects_gitignore(repo):
    (repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (repo / "ignored.txt").write_text("x", encoding="utf-8")
    (repo / "visible.py").write_text("def top():\n    pass\n", encoding="utf-8")

    rendered = render_map(repo)

    assert "visible.py" in rendered and "top" in rendered
    assert "ignored.txt" not in rendered


# --------------------------------------------------------------------------- 실행 통합


def go(repo, config, adapter):
    commit(repo)
    return run_dag(
        repo,
        config,
        Dag(load_tasks(repo)),
        adapter=adapter,
        run_id="run-1",
        scratch=repo.parent / "scratch",
        context_builder=ContextBuilder(repo, config),
    )


def test_context_selection_reduces_prompt_tokens(repo):
    """M4 완료 기준 — 전량 주입 대비 감소가 `total_tokens` 로 보고된다 (docs/12)."""
    config = configure(repo, profile="worktree")
    fat = "# Guide\n\n" + "\n\n".join(
        f"## Section {i}\n\n" + ("내용 " * 400) for i in range(10)
    )
    (repo / "docs").mkdir()
    (repo / "docs" / "GUIDE.md").write_text(fat, encoding="utf-8")
    write_task(
        repo, "T-001", allowed_paths=["src/**"], context={"docs": ["docs/GUIDE.md#section-3"]}
    )
    adapter = ScriptedAdapter([{"files": {"src/a.py": "1\n"}}])

    store = go(repo, config, adapter)

    assert store.state.tasks["T-001"].state is State.DONE
    manifest = json.loads(
        (store.run_dir / "tasks" / "T-001" / "context.manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["total_tokens"] < estimate_tokens(fat)  # 전량 주입보다 작다
    prompt = (store.run_dir / "tasks" / "T-001" / "prompt.md").read_text(encoding="utf-8")
    assert "Section 3" in prompt
    assert "Section 7" not in prompt


def test_the_dispatch_event_references_the_manifest(repo):
    config = configure(repo, profile="worktree")
    write_task(repo, "T-001", allowed_paths=["src/**"])
    adapter = ScriptedAdapter([{"files": {"src/a.py": "1\n"}}])

    store = go(repo, config, adapter)

    dispatched = next(e for e in store.journal.read() if e.type is EventType.TASK_DISPATCHED)
    reference = dispatched.payload["context_manifest_ref"]
    assert reference and Path(reference).is_file()


def test_the_cli_run_assembles_context(repo):
    from harness.cli import main

    configure(
        repo, scenario={"tasks": {"T-001": {"files": {"a.py": "1\n"}}}}, profile="worktree"
    )
    write_task(repo, "T-001")
    commit(repo)

    assert main(["run", "--repo", str(repo), "--run-id", "run-1"]) == 0
    manifest = repo / ".harness" / "runs" / "run-1" / "tasks" / "T-001" / "context.manifest.json"
    assert manifest.is_file()
