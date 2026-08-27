"""task 집합의 로딩과 의존 그래프.

task 계약은 docs/03 이, ready-set 규칙은 docs/05 가 canonical 이다.
"""

import textwrap

import pytest

from harness.dag import Dag, load_tasks
from harness.errors import TaskDefinitionError
from harness.models import ExecutionProfile, RiskLevel, TaskKind

MINIMAL = "id: {id}\nname: {id}\nkind: analysis\n"


def write_task(repo, task_id, body=None):
    directory = repo / "tasks"
    directory.mkdir(parents=True, exist_ok=True)
    text = textwrap.dedent(body) if body else MINIMAL.format(id=task_id)
    (directory / f"{task_id}.task.yaml").write_text(text, encoding="utf-8")


def chain(repo):
    """T-001 <- T-002 <- T-003."""
    write_task(repo, "T-001")
    write_task(repo, "T-002", "id: T-002\nname: b\nkind: analysis\ndepends_on: [T-001]\n")
    write_task(repo, "T-003", "id: T-003\nname: c\nkind: analysis\ndepends_on: [T-002]\n")
    return Dag(load_tasks(repo))


# --------------------------------------------------------------------------- 로딩


def test_tasks_load_from_the_documented_location(repo):
    """docs/03 파일 배치 — tasks/T-###.task.yaml"""
    write_task(repo, "T-001")
    assert list(load_tasks(repo)) == ["T-001"]


def test_loading_an_empty_task_directory_is_empty(repo):
    assert load_tasks(repo) == {}


def test_the_documented_task_example_loads(repo):
    write_task(
        repo,
        "T-003",
        """
        id: T-003
        name: api-layer
        kind: implementation
        satisfies: [R-002, R-005]
        depends_on: [T-001]
        risk: medium
        agent: default
        profile: worktree
        allowed_paths: ["src/api/**"]
        preconditions:
          - {kind: env_var, name: DATABASE_URL}
        acceptance:
          - cmd: ["npm", "run", "build"]
          - cmd: ["npm", "test"]
            expect_fail_before: true
        outputs:
          required: [public_api]
          optional: [decisions]
        spec_hash: "sha256:abc"
        """,
    )
    write_task(repo, "T-001")
    task = load_tasks(repo)["T-003"]

    assert task.kind is TaskKind.IMPLEMENTATION
    assert task.risk is RiskLevel.MEDIUM
    assert task.profile is ExecutionProfile.WORKTREE
    assert task.depends_on == ("T-001",)
    assert task.required_outputs == ("public_api",)
    assert task.optional_outputs == ("decisions",)
    assert task.acceptance[1].expect_fail_before is True
    assert task.acceptance[0].cmd == ("npm", "run", "build")


def test_an_undeclared_risk_stays_undeclared(repo):
    """선언되지 않은 값을 지어내지 않는다. effective_risk 는 docs/06 의 몫이다."""
    write_task(repo, "T-001")
    assert load_tasks(repo)["T-001"].risk is None


def test_an_undeclared_agent_and_profile_stay_undeclared(repo):
    """config 의 기본값으로 해석하는 것은 실행 시점의 일이다."""
    write_task(repo, "T-001")
    task = load_tasks(repo)["T-001"]
    assert task.agent is None and task.profile is None


def test_a_task_violating_the_schema_is_rejected(repo):
    """docs/03 — cmd 는 argv 리스트다. schemas/task.schema.json 이 계약이다."""
    write_task(repo, "T-001", 'id: T-001\nname: a\nkind: analysis\nacceptance:\n  - cmd: "npm test"\n')
    with pytest.raises(TaskDefinitionError):
        load_tasks(repo)


def test_a_filename_that_disagrees_with_the_id_is_rejected(repo):
    write_task(repo, "T-001", "id: T-002\nname: a\nkind: analysis\n")
    with pytest.raises(TaskDefinitionError):
        load_tasks(repo)


def test_unparsable_task_yaml_is_reported_with_its_path(repo):
    write_task(repo, "T-001", "id: [T-001\n")
    with pytest.raises(TaskDefinitionError, match="T-001"):
        load_tasks(repo)


# --------------------------------------------------------------------------- 그래프


def test_topological_order_respects_dependencies(repo):
    assert chain(repo).order() == ("T-001", "T-002", "T-003")


def test_order_is_deterministic_for_independent_tasks(repo):
    for task_id in ("T-003", "T-001", "T-002"):
        write_task(repo, task_id)
    assert Dag(load_tasks(repo)).order() == ("T-001", "T-002", "T-003")


def test_a_cycle_is_rejected(repo):
    write_task(repo, "T-001", "id: T-001\nname: a\nkind: analysis\ndepends_on: [T-002]\n")
    write_task(repo, "T-002", "id: T-002\nname: b\nkind: analysis\ndepends_on: [T-001]\n")
    with pytest.raises(TaskDefinitionError, match="순환"):
        Dag(load_tasks(repo))


def test_a_dependency_on_a_missing_task_is_rejected(repo):
    write_task(repo, "T-001", "id: T-001\nname: a\nkind: analysis\ndepends_on: [T-999]\n")
    with pytest.raises(TaskDefinitionError, match="T-999"):
        Dag(load_tasks(repo))


# --------------------------------------------------------------------------- ready-set


def test_ready_set_holds_tasks_whose_dependencies_are_all_verified(repo):
    """docs/05 — ready_set = { t | t.depends_on 이 전부 verified }"""
    dag = chain(repo)
    remaining = {"T-001", "T-002", "T-003"}
    assert dag.ready(verified=set(), remaining=remaining) == ("T-001",)
    assert dag.ready(verified={"T-001"}, remaining=remaining - {"T-001"}) == ("T-002",)


def test_a_task_already_handled_is_not_ready_again(repo):
    dag = chain(repo)
    assert dag.ready(verified={"T-001"}, remaining={"T-002", "T-003"}) == ("T-002",)
    assert dag.ready(verified={"T-001", "T-002"}, remaining=set()) == ()


def test_independent_tasks_are_all_ready_at_once(repo):
    for task_id in ("T-001", "T-002"):
        write_task(repo, task_id)
    dag = Dag(load_tasks(repo))
    assert dag.ready(verified=set(), remaining={"T-001", "T-002"}) == ("T-001", "T-002")


def test_descendants_are_what_a_blocked_task_blocks(repo):
    """docs/03 — blocked 는 해당 task 와 그 하위 의존만 막는다."""
    assert chain(repo).descendants("T-001") == frozenset({"T-002", "T-003"})


def test_a_leaf_blocks_nothing(repo):
    assert chain(repo).descendants("T-003") == frozenset()


def test_a_blocked_branch_does_not_block_a_sibling(repo):
    write_task(repo, "T-001")
    write_task(repo, "T-002", "id: T-002\nname: b\nkind: analysis\ndepends_on: [T-001]\n")
    write_task(repo, "T-003")  # 독립 가지
    assert Dag(load_tasks(repo)).descendants("T-001") == frozenset({"T-002"})


def test_dag_does_not_import_higher_layers():
    from harness import dag as dag_module

    source = open(dag_module.__file__, encoding="utf-8").read()
    for forbidden in ("harness.exec", "harness.cli", "harness.eval", "harness.store"):
        assert forbidden not in source
