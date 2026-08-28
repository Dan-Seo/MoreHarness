"""구현 전 게이트 — 코드는 보지 않는다. docs/08 이 canonical 이다.

실패는 `run` 을 막고 경고는 보고만 한다. 게이트는 `.harness/analyze.json` 이 있을
때만 작동한다 — 커널은 analyze 없이도 동작해야 한다 (docs/02 의 옵션 경계).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

import yaml

from harness.config import HARNESS_DIR, Config
from harness.dag import Dag, load_tasks
from harness.errors import TaskDefinitionError
from harness.models import Task, TaskKind
from harness.policy import CommandPolicy, PolicyVerdict, load_approvals
from harness.spec import NEEDS, load_specs, spec_hash

HARNESS_FIELDS = ("changed_files", "created_files", "diff_stat")
REPORT = "analyze-report.md"
RECORD = "analyze.json"


@dataclass(frozen=True)
class Report:
    failures: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.failures


def analyze(repo: Path | str, config: Config) -> Report:
    repo = Path(repo)
    failures: list[str] = []
    warnings: list[str] = []

    try:
        specs = load_specs(repo)
    except yaml.YAMLError as exc:
        return Report((f"spec 을 읽을 수 없다: {exc}",), ())
    try:
        tasks = load_tasks(repo)
        dag = Dag(tasks)
    except TaskDefinitionError as exc:
        return Report((str(exc),), ())

    requirements: dict[str, Path] = {}
    for path, data in specs.items():
        if NEEDS in path.read_text(encoding="utf-8"):
            failures.append(f"{path.parent.name}: {NEEDS} 이(가) 남아 있다")
        for requirement in data.get("requirements") or ():
            rid = requirement.get("id") if isinstance(requirement, dict) else None
            if not rid:
                continue
            if rid in requirements:
                failures.append(f"{rid} 이(가) 두 spec 에 있다 — R-### 는 유일해야 한다")
            requirements[rid] = path

    policy = CommandPolicy.from_config(
        config.command_policy, load_approvals(repo / HARNESS_DIR / "approved_commands.yaml")
    )

    satisfied: set[str] = set()
    for task in tasks.values():
        for rid in task.satisfies:
            satisfied.add(rid)
            if rid not in requirements:
                failures.append(f"{task.id}: 존재하지 않는 {rid} 를 satisfies 한다")
        if specs and task.kind is TaskKind.IMPLEMENTATION and not task.satisfies:
            warnings.append(f"{task.id}: 어떤 요구사항도 만족시키지 않는 고아 task 다")

        if task.kind is TaskKind.IMPLEMENTATION and not task.acceptance:
            failures.append(f"{task.id}: implementation task 에 AC 가 없다")
        for criterion in task.acceptance:
            program = criterion.cmd[0] if criterion.cmd else ""
            if not program or (shutil.which(program) is None and not Path(program).exists()):
                failures.append(f"{task.id}: 실행 파일을 찾을 수 없다 — {program!r}")
        if (
            task.kind is TaskKind.IMPLEMENTATION
            and task.acceptance
            and not any(criterion.expect_fail_before for criterion in task.acceptance)
        ):
            warnings.append(f"{task.id}: expect_fail_before 없는 AC 뿐이다 — 검증력이 의심된다")

        for cmd, shell in _commands(task):
            decision = policy.decide(cmd, shell=shell)
            joined = " ".join(cmd)
            if decision.verdict is PolicyVerdict.DENY:
                failures.append(f"{task.id}: deny 커맨드 — {joined}")
            elif decision.verdict is PolicyVerdict.REQUIRE_APPROVAL and decision.approver is None:
                warnings.append(f"{task.id}: 승인 필요 커맨드 — {joined}")

        for name in task.required_outputs:
            if name in HARNESS_FIELDS:
                failures.append(f"{task.id}: 하네스 산출 필드 {name} 는 required 가 될 수 없다")
        if task.required_outputs and not any(
            task.id in other.depends_on for other in tasks.values()
        ):
            warnings.append(f"{task.id}: required output 을 소비할 downstream 이 없다")

        failures += _drift(task, requirements, warnings)

    for rid in requirements:
        if rid not in satisfied:
            failures.append(f"{rid}: 어떤 task 도 이 요구사항을 만족시키지 않는다")

    if config.max_parallel > 1:
        warnings += _parallel_conflicts(dag, tasks, config)

    return Report(tuple(failures), tuple(warnings))


def _drift(task: Task, requirements: dict[str, Path], warnings: list[str]) -> list[str]:
    if not task.satisfies:
        return []
    spec_path = next((requirements[rid] for rid in task.satisfies if rid in requirements), None)
    if spec_path is None:
        return []  # satisfies 자체가 이미 실패로 보고되었다
    if task.spec_hash is None:
        warnings.append(f"{task.id}: spec_hash 가 없어 드리프트를 검출할 수 없다")
        return []
    if task.spec_hash != spec_hash(spec_path):
        return [f"{task.id}: spec_hash 가 현재 spec 과 다르다 (드리프트)"]
    return []


def _commands(task: Task) -> Iterator[tuple[tuple[str, ...], bool]]:
    for criterion in task.acceptance:
        if criterion.cmd:
            yield criterion.cmd, criterion.shell
    for precondition in task.preconditions:
        if precondition.get("kind") == "command" and precondition.get("cmd"):
            yield tuple(precondition["cmd"]), bool(precondition.get("shell", False))


def _parallel_conflicts(dag: Dag, tasks: dict[str, Task], config: Config) -> list[str]:
    from harness.exec.scheduler import conflicts  # 같은 옵션 계층이다 (docs/02)

    out = []
    ids = list(dag.order())
    for index, lhs in enumerate(ids):
        for rhs in ids[index + 1 :]:
            if lhs in dag.descendants(rhs) or rhs in dag.descendants(lhs):
                continue
            if conflicts(tasks[lhs], tasks[rhs], config.default_profile):
                out.append(
                    f"{lhs} 와 {rhs} 는 병렬 가능하지만 경로가 겹친다 — 직렬화된다 (05)"
                )
    return out


# --------------------------------------------------------------------------- 산출과 게이트


def fingerprint(repo: Path | str) -> str:
    """specs/ 와 tasks/ 파일 내용의 해시. run 게이트의 신선도 판단에 쓴다."""
    repo = Path(repo)
    digest = hashlib.sha256()
    for path in sorted([*repo.glob("specs/*/spec.yaml"), *repo.glob("tasks/*.task.yaml")]):
        digest.update(path.relative_to(repo).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def write_report(repo: Path | str, report: Report) -> Path:
    repo = Path(repo)
    lines = ["# analyze report", "", f"- 결과: {'통과' if report.ok else '실패'}", ""]
    if report.failures:
        lines += ["## 실패 — run 을 막는다", "", *[f"- {item}" for item in report.failures], ""]
    if report.warnings:
        lines += ["## 경고", "", *[f"- {item}" for item in report.warnings], ""]
    path = repo / HARNESS_DIR / REPORT
    path.write_text("\n".join(lines), encoding="utf-8")
    (repo / HARNESS_DIR / RECORD).write_text(
        json.dumps(
            {
                "ok": report.ok,
                "fingerprint": fingerprint(repo),
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def gate(repo: Path | str) -> str | None:
    """docs/08 — `analyze.json` 이 있을 때만 게이트다. 없으면 쓰지 않는 저장소다."""
    record_path = Path(repo) / HARNESS_DIR / RECORD
    if not record_path.is_file():
        return None
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except ValueError:
        return "analyze.json 을 읽을 수 없다 — harness analyze 를 다시 실행하라"
    if record.get("fingerprint") != fingerprint(repo):
        return "spec/tasks 가 analyze 이후 바뀌었다 — harness analyze 를 다시 실행하라"
    if not record.get("ok"):
        return "analyze 가 실패한 상태다 — .harness/analyze-report.md 를 보고 고친 뒤 다시 실행하라"
    return None
