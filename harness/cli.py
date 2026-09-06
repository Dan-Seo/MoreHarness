"""진입점.

docs/02 — `sys.exit` 는 이 파일에만 존재한다. 다른 모듈은 예외를 올리거나 값을 반환한다.
커맨드는 마일스톤마다 늘어난다 — M0 이 `init`·`status`·`doctor`, M1 이 `run`,
M2 가 `run --resume` 이다 (docs/12).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

from harness.adapters.registry import build
from harness.config import HARNESS_DIR, Config, load
from harness.dag import Dag, load_tasks
from harness.errors import (
    AdapterNotFoundError,
    ConfigError,
    HarnessError,
    JournalCorruptionError,
    NotARepositoryError,
)
from harness.exec.runner import run_dag
from harness.exec.workspace import repo_scratch
from harness.git import git, is_repo, repo_root
from harness.models import RunState, State
from harness.store import Journal, Store, check_sequence, fold

REQUIRED_CONTROL_PLANE = (
    "config.yaml",
    "constitution.md",
    "approved_commands.yaml",
    "knowledge",
    "runs",
)
CONTROL_PLANE_DIRS = ("knowledge", "runs")

# 정규식을 담으므로 raw 문자열이다. 규칙 목록의 canonical 은 docs/06 이다.
DEFAULT_CONFIG = r"""# .harness/config.yaml — 하네스가 항상 메인 저장소에서 읽는 control-plane 설정.
# 키의 canonical 정의는 docs/03 의 `config.yaml — canonical` 이다.
version: 1

defaults:
  adapter: mock
  profile: worktree
  max_parallel: 1

allow_unsafe: false

adapters:
  mock:
    type: mock

# 하네스가 실행하는 모든 커맨드가 이 정책을 통과한다 (docs/06).
# 무엇이 자동 실행 승인되었는지는 여기 보이는 것이 전부다. 검토하고 고쳐서 쓴다.
command_policy:
  default: require_approval          # fail-closed
  rules:                             # 첫 매치 우선
    - {match: '^(npm|pnpm|yarn) (test|run (build|lint|typecheck))$', verdict: allow}
    - {match: '^(pytest|python -m pytest)', verdict: allow}
    - {match: '^git (status|diff|log)', verdict: allow}
    - {match: 'rm\s+-rf|git\s+push\s+--force|git\s+reset\s+--hard|^sudo|DROP\s+DATABASE|id_rsa', verdict: deny}
    - {match: '^(curl|wget|npm install|pip install|terraform apply|kubectl apply)', verdict: require_approval}
"""

DEFAULT_CONSTITUTION = """\
# Constitution

이 프로젝트가 절대 어기지 않는 규칙 (docs/01).

control-plane 문서이므로 항상 trusted 로 취급되고, 모든 task 프롬프트에 포함되며
예산 때문에 잘리지 않는다. 그러니 짧게 유지한다.

## 규칙

- (프로젝트 규칙을 여기에 적는다)
"""

DEFAULT_APPROVED_COMMANDS = """\
# .harness/approved_commands.yaml — require_approval 커맨드의 승인 기록 (docs/06).
approvals: []
"""

# docs/03 — 커밋되는 것은 사람이 쓴 입력뿐이고 run 산출물은 아니다.
DEFAULT_GITIGNORE = """\
# .harness/ 의 run 산출물은 커밋하지 않는다 (docs/03 의 파일 배치).
# 커밋되는 것은 config.yaml · constitution.md · approved_commands.yaml ·
# waivers.yaml · knowledge/ 이며, 아래는 실행이 만들어 내는 것들이다.
runs/
analyze.json
analyze-report.md
converge.json
coverage.md
ship-report.md
"""

CONTROL_PLANE_FILES = {
    ".gitignore": DEFAULT_GITIGNORE,
    "config.yaml": DEFAULT_CONFIG,
    "constitution.md": DEFAULT_CONSTITUTION,
    "approved_commands.yaml": DEFAULT_APPROVED_COMMANDS,
}


def main(argv: list[str] | None = None) -> int:
    # Windows pipes may use cp949 (or ASCII). Keep the caller's encoding, but
    # escape unsupported characters instead of crashing after a successful action.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse 의 종료를 종료 코드로 바꾼다
        return int(exc.code or 2)

    handlers = {
        "init": _init,
        "spec": _spec,
        "clarify": _clarify,
        "plan": _plan,
        "tasks": _tasks,
        "analyze": _analyze,
        "run": _run,
        "converge": _converge,
        "ship": _ship,
        "eval": _eval,
        "learn": _learn,
        "status": _status,
        "doctor": _doctor,
    }
    try:
        return handlers[args.command](args)
    except ImportError as exc:
        # 옵션 레이어가 없는 설치본이다 (docs/02). 커맨드가 없는 것이지 결함이 아니다.
        print(f"이 커맨드에는 옵션 모듈이 필요하다: {exc}")
        return 1


def run_cli() -> None:
    sys.exit(main(sys.argv[1:]))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness", description="Agents propose. Harness verifies.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="저장소에 .harness/ 를 만든다")
    init.add_argument("--repo", help="저장소 루트 (기본: 현재 위치의 저장소)")

    spec_cmd = sub.add_parser("spec", help="Intent → Spec 골격 (R-### 부여)")
    spec_cmd.add_argument("intent", help="한 문장 의도")
    spec_cmd.add_argument("--slug", help="specs/<slug>/ 이름 (기본: intent 에서 만든다)")
    spec_cmd.add_argument("--repo", help="저장소 루트 (기본: 현재 위치의 저장소)")

    clarify = sub.add_parser("clarify", help="[NEEDS CLARIFICATION] 해소")
    clarify.add_argument("--repo", help="저장소 루트 (기본: 현재 위치의 저장소)")

    plan = sub.add_parser("plan", help="Spec → Plan 골격")
    plan.add_argument("--repo", help="저장소 루트 (기본: 현재 위치의 저장소)")

    tasks_cmd = sub.add_parser("tasks", help="미할당 요구사항의 task 골격")
    tasks_cmd.add_argument("--repo", help="저장소 루트 (기본: 현재 위치의 저장소)")

    analyze_cmd = sub.add_parser("analyze", help="구현 전 게이트 — 실패하면 run 을 막는다")
    analyze_cmd.add_argument("--repo", help="저장소 루트 (기본: 현재 위치의 저장소)")

    run = sub.add_parser("run", help="task DAG 를 실행한다")
    run.add_argument("--repo", help="저장소 루트 (기본: 현재 위치의 저장소)")
    run.add_argument("--run-id", help="run 디렉토리 이름 (기본: 시각으로 만든다)")
    run.add_argument("--resume", metavar="RUN-ID", help="죽은 run 을 이어서 실행한다")

    converge_cmd = sub.add_parser("converge", help="구현 후 게이트 — 커버리지·드리프트·debt")
    converge_cmd.add_argument("--repo", help="저장소 루트 (기본: 현재 위치의 저장소)")
    converge_cmd.add_argument("--run", help="대상 run-id (기본: 가장 최근)")

    ship_cmd = sub.add_parser("ship", help="통합·배포 게이트 — 사용자 브랜치로 머지")
    ship_cmd.add_argument("--repo", help="저장소 루트 (기본: 현재 위치의 저장소)")
    ship_cmd.add_argument("--run", help="대상 run-id (기본: 가장 최근)")

    eval_cmd = sub.add_parser("eval", help="능력 eval — fixtures × arms × repeat")
    eval_sub = eval_cmd.add_subparsers(dest="eval_command", required=True)
    eval_run = eval_sub.add_parser("run", help="hidden grader 로 arm 들을 비교한다")
    eval_run.add_argument("--fixtures", required=True, help="fixture 디렉토리들의 부모")
    eval_run.add_argument("--arms", required=True, help="쉼표로 구분한 arm 목록")
    eval_run.add_argument("--repeat", type=int, default=3)
    eval_run.add_argument("--out", help="리포트를 쓸 위치 (기본: --fixtures 디렉토리)")

    eval_import = eval_sub.add_parser("import", help="외부 벤치마크 → fixture 변환")
    eval_import.add_argument("--benchmark", required=True, choices=("swebench",))
    eval_import.add_argument("--instances", required=True, help="인스턴스 JSON Lines 파일")
    eval_import.add_argument("--repos", required=True, help="로컬 체크아웃들이 있는 디렉토리")
    eval_import.add_argument("--out", required=True, help="fixture 를 쓸 디렉토리")

    learn_cmd = sub.add_parser("learn", help="지식 카드 — 후보 제안, 승격, 폐기")
    learn_cmd.add_argument("action", nargs="?", choices=("promote", "retire"), help="사람의 판단")
    learn_cmd.add_argument("card", nargs="?", help="K-### (promote/retire 에 필요하다)")
    learn_cmd.add_argument("--repo", help="저장소 루트 (기본: 현재 위치의 저장소)")

    status = sub.add_parser("status", help="현재 run 상태, open_debts, human_required")
    status.add_argument("--repo", help="저장소 루트 (기본: 현재 위치의 저장소)")
    status.add_argument("--run", help="볼 run-id (기본: 가장 최근)")

    doctor = sub.add_parser("doctor", help="일관성 검사 및 복구")
    doctor.add_argument("--repo", help="저장소 루트 (기본: 현재 위치의 저장소)")
    return parser


def _resolve_repo(args: argparse.Namespace) -> Path:
    if args.repo:
        return Path(args.repo)
    try:
        return repo_root(Path.cwd())
    except NotARepositoryError:
        return Path.cwd()


def _run_ids(repo: Path) -> list[str]:
    runs_dir = repo / HARNESS_DIR / "runs"
    if not runs_dir.is_dir():
        return []
    return sorted(entry.name for entry in runs_dir.iterdir() if entry.is_dir())


# --------------------------------------------------------------------------- init


def _init(args: argparse.Namespace) -> int:
    """docs/03 의 파일 배치대로 control-plane 을 만든다.

    이미 있는 것은 건드리지 않는다. `runs/` 는 저장소에 커밋되지 않으므로 clone 뒤
    다시 만들어야 하고, 그래서 이 커맨드는 몇 번을 실행해도 안전해야 한다.
    """
    repo = _resolve_repo(args)
    if not is_repo(repo):
        print(f"{repo} 은(는) git 저장소가 아니다 — .harness/ 를 둘 곳이 없다")
        return 1

    harness_dir = repo / HARNESS_DIR
    for name in CONTROL_PLANE_DIRS:
        path = harness_dir / name
        print(f"{'exists ' if path.is_dir() else 'created'}  {HARNESS_DIR}/{name}/")
        path.mkdir(parents=True, exist_ok=True)

    for name, content in CONTROL_PLANE_FILES.items():
        path = harness_dir / name
        if path.exists():
            print(f"exists   {HARNESS_DIR}/{name}")
            continue
        path.write_text(content, encoding="utf-8")
        print(f"created  {HARNESS_DIR}/{name}")

    print(f"{HARNESS_DIR}/ 준비됨 — harness doctor 로 확인한다")
    return 0


# --------------------------------------------------------------------------- run


def _run(args: argparse.Namespace) -> int:
    """DAG 를 실행한다. `--resume` 은 같은 run-id 로 몇 번을 돌려도 결과가 같다 (docs/10).

    일부가 막혀도 run 자체는 정상 종료한다 (docs/10). 종료 코드는 사람이 볼 것이
    남았는지를 알린다 — 전부 done 이면 0 이다.
    """
    repo = _resolve_repo(args)
    try:
        blocked = _analyze_gate(repo)
        if blocked:
            print(f"실행할 수 없다: {blocked}")
            return 1

        config = load(repo)
        dag = Dag(load_tasks(repo))
        stages = _optional_stages(repo, config)
        if stages.missing:
            print(f"옵션 없이 돈다 (docs/02): {', '.join(stages.missing)}")
        store = stages.execute(
            repo,
            config,
            dag,
            run_id=args.resume or args.run_id,
            resume=bool(args.resume),
            context_builder=stages.builder,
            review_stage=stages.review,
            tdd_stage=stages.tdd,
        )
    except HarnessError as exc:
        print(f"실행할 수 없다: {exc}")
        return 1

    _print_state(store.state)
    return 0 if all(t.state is State.DONE for t in store.state.tasks.values()) else 1


def _analyze_gate(repo: Path) -> str:
    """docs/08 의 구현 전 게이트. 기록이 있을 때만 작동하고, 옵션이므로 없으면 게이트도 없다."""
    try:
        from harness.analyze import gate
    except ImportError:
        return ""
    return gate(repo)


@dataclass(frozen=True)
class _Stages:
    """cli 가 조립한 옵션 레이어. 커널에 주입되는 것 전부다."""

    builder: Any = None
    review: Any = None
    tdd: Any = None
    execute: Any = run_dag
    missing: tuple[str, ...] = ()


def _optional_stages(repo: Path, config: Config) -> _Stages:
    """옵션 레이어 (docs/02) — cli 가 조립해 커널에 주입한다. 커널은 import 하지 않는다.

    없으면 없는 채로 돈다. 그것이 M8 의 완료 기준이다 — 옵션을 전부 제거해도 커널은
    파이프라인을 끝까지 돌린다. 무엇이 빠졌는지는 사람에게 말한다.
    """
    stages = {}
    missing = []
    for key, name, module_path, attribute in (
        ("builder", "context", "harness.context.builder", "ContextBuilder"),
        ("review", "review", "harness.exec.review", "ReviewStage"),
        ("tdd", "tdd", "harness.exec.tdd", "TddStage"),
    ):
        try:
            module = import_module(module_path)
        except ImportError:
            missing.append(name)
        else:
            stages[key] = getattr(module, attribute)(repo, config)

    execute = run_dag
    if config.max_parallel > 1:
        try:
            execute = import_module("harness.exec.scheduler").run_dag
        except ImportError:
            missing.append("scheduler")
    return _Stages(**stages, execute=execute, missing=tuple(missing))


# --------------------------------------------------------------------------- 스펙 파이프라인 (M6)


def _spec(args: argparse.Namespace) -> int:
    from harness.spec import create_spec

    try:
        path = create_spec(_resolve_repo(args), args.intent, args.slug)
    except HarnessError as exc:
        print(f"만들 수 없다: {exc}")
        return 1
    print(f"{path} 생성됨 — [NEEDS CLARIFICATION] 을 채운 뒤 harness clarify 로 확인한다")
    return 0


def _clarify(args: argparse.Namespace) -> int:
    from harness.spec import open_questions, resolve_question

    repo = _resolve_repo(args)
    questions = open_questions(repo)
    if not questions:
        print("남은 [NEEDS CLARIFICATION] 이 없다")
        return 0

    if sys.stdin.isatty():
        for path, question in questions:
            print(f"{path}: {question}")
            answer = input("답> ").strip()
            if answer:
                resolve_question(path, question, answer)
        questions = open_questions(repo)
        if not questions:
            print("전부 해소되었다")
            return 0

    for path, question in questions:
        print(f"{path}: {question}")
    print(f"{len(questions)}개가 남아 있다 — spec 을 고치거나 TTY 에서 답한다")
    return 1


def _plan(args: argparse.Namespace) -> int:
    from harness.spec import create_plans

    created = create_plans(_resolve_repo(args))
    for path in created:
        print(f"{path} 생성됨")
    if not created:
        print("만들 plan 이 없다 — spec 이 없거나 plan 이 이미 있다")
    return 0


def _tasks(args: argparse.Namespace) -> int:
    from harness.spec import scaffold_tasks

    created = scaffold_tasks(_resolve_repo(args))
    for path in created:
        print(f"{path} 생성됨 — acceptance 를 채우기 전에는 analyze 가 막는다")
    if not created:
        print("만들 task 가 없다 — 모든 요구사항이 이미 할당되어 있다")
    return 0


def _analyze(args: argparse.Namespace) -> int:
    from harness.analyze import analyze, write_report

    repo = _resolve_repo(args)
    try:
        config = load(repo)
        report = analyze(repo, config)
    except HarnessError as exc:
        print(f"analyze 할 수 없다: {exc}")
        return 1
    path = write_report(repo, report)
    for failure in report.failures:
        print(f"[실패] {failure}")
    for warning in report.warnings:
        print(f"[경고] {warning}")
    print(f"{'통과' if report.ok else '실패'} — {path}")
    return 0 if report.ok else 1


def _converge(args: argparse.Namespace) -> int:
    from harness.converge import converge

    repo = _resolve_repo(args)
    try:
        config = load(repo)
        report = converge(repo, config, args.run)
    except HarnessError as exc:
        print(f"converge 할 수 없다: {exc}")
        return 1
    for rid, status in report.coverage.items():
        print(f"{rid}: {status}")
    for failure in report.failures:
        print(f"[실패] {failure}")
    if report.open_debts:
        print(
            f"open_debts: {', '.join(report.open_debts)} — ship 이 막는다 "
            "(waiver 는 .harness/waivers.yaml)"
        )
    print("통과" if report.ok else "실패")
    return 0 if report.ok else 1


def _ship(args: argparse.Namespace) -> int:
    from harness.converge import ship

    repo = _resolve_repo(args)
    try:
        config = load(repo)
        report = ship(repo, config, args.run)
    except HarnessError as exc:
        print(f"ship 할 수 없다: {exc}")
        return 1
    if report.waived:
        print(f"waived: {', '.join(report.waived)}")
    print(report.detail)
    return 0 if report.ok else 1


# --------------------------------------------------------------------------- eval (M7)


def _eval(args: argparse.Namespace) -> int:
    if args.eval_command == "import":
        return _eval_import(args)

    import tempfile

    from harness.eval import arms as arms_mod
    from harness.eval import fixtures as fixtures_mod
    from harness.eval import report as report_mod

    found = fixtures_mod.discover(args.fixtures)
    if not found:
        print(f"{args.fixtures} 에서 fixture 를 찾지 못했다")
        return 1
    arm_names = [name.strip() for name in args.arms.split(",") if name.strip()]
    if not arm_names:
        print("--arms 가 비어 있다")
        return 1

    workdir = Path(tempfile.mkdtemp(prefix="harness-eval-"))
    try:
        results = arms_mod.run_matrix(found, arm_names, args.repeat, workdir)
    except HarnessError as exc:
        print(f"eval 할 수 없다: {exc}")
        return 1

    payload = report_mod.build(results, repeat=args.repeat, workdir=workdir)
    json_path, md_path = report_mod.write(args.out or args.fixtures, payload)
    for arm, entry in payload["arms"].items():
        rate = entry["grader_success_rate"]
        print(f"{arm}: grader_success_rate {'n/a' if rate is None else format(rate, '.2f')}")
    print(f"{md_path} · {json_path}")
    return 0


def _eval_import(args: argparse.Namespace) -> int:
    """docs/11 — 없는 체크아웃은 건너뛰고 보고한다. 하나가 없다고 전체가 실패하지 않는다."""
    from harness.eval import swebench

    try:
        report = swebench.convert(args.instances, args.repos, args.out)
    except HarnessError as exc:
        print(str(exc))
        return 1

    for instance_id in report.created:
        print(f"변환  {instance_id}")
    for instance_id, reason in report.skipped:
        print(f"건너뜀  {instance_id} — {reason}")
    print(f"{len(report.created)} 개 변환, {len(report.skipped)} 개 건너뜀 → {args.out}")
    return 0


# --------------------------------------------------------------------------- status


def _learn(args: argparse.Namespace) -> int:
    """docs/08 — 하네스는 제안하고 사람이 승격·폐기한다."""
    from harness import learn as learn_module

    repo = _resolve_repo(args)
    if args.action:
        if not args.card:
            print(f"{args.action} 에는 카드 id 가 필요하다 — harness learn {args.action} K-001")
            return 2
        try:
            path = (learn_module.promote if args.action == "promote" else learn_module.retire)(
                repo, args.card
            )
        except HarnessError as exc:
            print(str(exc))
            return 1
        print(f"{args.card} → {args.action}d ({path})")
        return 0

    try:
        config = load(repo)
    except HarnessError as exc:
        print(str(exc))
        return 1

    report = learn_module.learn(repo, config)
    for card_id in report.proposed:
        print(f"제안  {card_id} — claim 을 채운 뒤 harness learn promote {card_id}")
    for card_id in report.updated:
        print(f"보강  {card_id} — evidence 추가")
    for card_id in report.retire_candidates:
        print(f"폐기?  {card_id} — 오래 쓰이지 않았다. harness learn retire {card_id}")
    if not (report.proposed or report.updated or report.retire_candidates):
        print("새 후보 없음")
    return 0


def _status(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args)
    run_ids = _run_ids(repo)

    if args.run:
        if args.run not in run_ids:
            print(f"run {args.run} 을(를) {repo / HARNESS_DIR / 'runs'} 에서 찾을 수 없다")
            return 1
        run_id = args.run
    elif not run_ids:
        print(f"no runs — {repo / HARNESS_DIR / 'runs'} 가 비어 있다")
        return 0
    else:
        run_id = run_ids[-1]

    try:
        store = Store(repo / HARNESS_DIR / "runs" / run_id)
    except JournalCorruptionError as exc:
        print(f"journal 손상으로 상태를 읽을 수 없다: {exc}")
        return 1

    _print_state(store.state)
    return 0


def _print_state(state: RunState) -> None:
    print(f"run  {state.run_id}")
    print(f"     started  {state.started_at or '-'}")
    print(f"     finished {state.finished_at or '-'}")

    print("tasks")
    if not state.tasks:
        print("  (없음)")
    for task in state.tasks.values():
        verdict = str(task.verdict) if task.verdict else "-"
        line = f"  {task.task_id:8} {str(task.state):20} verdict={verdict:18} attempt={task.attempt}"
        if task.reason:
            line += f"  reason={task.reason}"
        print(line)

    print("open_debts")
    if not state.open_debts:
        print("  (없음)")
    for debt in state.open_debts.values():
        print(f"  {debt.debt_id}  {' '.join(debt.cmd)}  (origin {debt.origin_task})")

    print("human_required")
    if not state.human_required:
        print("  (없음)")
    for task_id in state.human_required:
        print(f"  {task_id}")


# --------------------------------------------------------------------------- doctor


class _Report:
    """검사 결과. ok 가 아닌 항목이 하나라도 있으면 종료 코드가 0 이 아니다."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.clean = True

    def ok(self, label: str, detail: str) -> None:
        self.lines.append(f"[ok ] {label:24} {detail}")

    def fixed(self, label: str, detail: str) -> None:
        self.lines.append(f"[fix] {label:24} {detail}")
        self.clean = False

    def bad(self, label: str, detail: str) -> None:
        self.lines.append(f"[bad] {label:24} {detail}")
        self.clean = False


def _doctor(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args)
    report = _Report()

    _check_repository(report, repo)
    config = _check_control_plane(report, repo)
    _check_adapters(report, repo, config)
    for run_id in _run_ids(repo):
        _check_run(report, repo / HARNESS_DIR / "runs" / run_id, run_id)
    _check_workspaces(report, repo)

    for line in report.lines:
        print(line)
    return 0 if report.clean else 1


def _check_repository(report: _Report, repo: Path) -> None:
    if is_repo(repo):
        report.ok("git 저장소", str(repo))
    else:
        report.bad("git 저장소", f"{repo} 은(는) git 저장소가 아니다")


def _check_control_plane(report: _Report, repo: Path):
    harness_dir = repo / HARNESS_DIR
    if not harness_dir.is_dir():
        report.bad(".harness/ 구조", f"{harness_dir} 가 없다 — harness init 으로 만든다")
    else:
        missing = [name for name in REQUIRED_CONTROL_PLANE if not (harness_dir / name).exists()]
        if missing:
            report.bad(".harness/ 구조", "없음: " + ", ".join(missing) + " — harness init 으로 만든다")
        else:
            report.ok(".harness/ 구조", f"필수 항목 {len(REQUIRED_CONTROL_PLANE)}개 존재")

    try:
        config = load(repo)
    except ConfigError as exc:
        report.bad("config.yaml", str(exc))
        return None
    report.ok(
        "config.yaml",
        f"version={config.version} adapter={config.default_adapter} profile={config.default_profile}",
    )
    return config


def _check_adapters(report: _Report, repo: Path, config) -> None:
    """docs/10 — 등록된 모든 어댑터에 preflight 를 실행하고 분류표 기준으로 보고한다."""
    if config is None:
        report.bad("adapter preflight", "config 를 읽지 못해 실행하지 못했다")
        return

    for name, adapter_config in config.adapters.items():
        label = f"adapter preflight {name}"
        try:
            adapter = build(name, adapter_config.type, adapter_config.options)
            result = adapter.preflight()
        except (AdapterNotFoundError, OSError, ValueError) as exc:
            report.bad(label, f"system defect (error): {exc}")
            continue

        if result.ok:
            report.ok(label, result.detail)
        elif result.kind == "missing_prerequisite":
            report.bad(label, f"prerequisite (blocked): {result.detail}")
        else:
            report.bad(label, f"system defect (error): {result.detail}")


def _check_run(report: _Report, run_dir: Path, run_id: str) -> None:
    try:
        events = Journal(run_dir / "journal.jsonl", run_id).read()
    except JournalCorruptionError as exc:
        report.bad(run_id, f"journal 손상: {exc}")
        return

    problems = check_sequence(events)
    if problems:
        report.bad(run_id, "journal " + "; ".join(problems))

    expected = fold(events, run_id)
    if _read_snapshot(run_dir / "state.json") == expected:
        last = events[-1].seq if events else 0
        report.ok(run_id, f"state == fold(journal), seq 1..{last}")
        return

    Store(run_dir).rebuild()  # journal 은 건드리지 않는다. state 는 캐시다.
    report.fixed(run_id, "state != fold(journal) — journal 기준으로 재구성했다")


def _check_workspaces(report: _Report, repo: Path) -> None:
    """docs/10 — 고아 워크트리와 미아 outbox.

    scratch 는 저장소별로 나뉘어 있으므로 (docs/03 의 `<repo-key>`) 여기서 지우는 것은
    이 저장소의 것뿐이다.
    """
    scratch = repo_scratch(repo)
    known = set(_run_ids(repo))

    orphans = []
    if scratch.is_dir():
        orphans = [d for d in sorted(scratch.iterdir()) if d.is_dir() and d.name not in known]
    for directory in orphans:
        _unregister_worktrees(repo, directory)
        shutil.rmtree(directory, ignore_errors=True)

    stray = _drop_finished_outboxes(repo, scratch, known)

    # 고아는 크래시가 남긴 것이므로 지적한다. 끝난 task 의 outbox 를 치우는 것은
    # 일상적인 청소이며 사람이 볼 것이 아니다.
    if orphans:
        report.fixed("고아 워크트리", f"어느 run 에도 속하지 않는 {len(orphans)}개를 지웠다")
    if stray:
        report.ok("미아 outbox", f"끝난 task 의 attempt 디렉토리 {stray}개를 지웠다")
    elif not orphans:
        report.ok("워크스페이스", f"{scratch} 에 정리할 것이 없다")


def _drop_finished_outboxes(repo: Path, scratch: Path, known: set[str]) -> int:
    """승격이 끝난 outbox 를 지운다. 끝나지 않은 task 의 것은 사람이 볼 수 있게 남긴다."""
    dropped = 0
    for run_id in known:
        state = _read_snapshot(repo / HARNESS_DIR / "runs" / run_id / "state.json")
        if state is None or state.finished_at is None:
            continue
        for task_id, task in state.tasks.items():
            outbox = scratch / run_id / task_id / "outbox"
            if task.state is State.DONE and outbox.is_dir():
                shutil.rmtree(outbox, ignore_errors=True)
                dropped += 1
    return dropped


def _unregister_worktrees(repo: Path, directory: Path) -> None:
    """디렉토리만 지우면 git 이 유령 워크트리를 기억한다. 등록을 먼저 푼다."""
    for worktree in directory.rglob("worktree"):
        if worktree.is_dir():
            git(["worktree", "remove", "--force", str(worktree)], cwd=repo)
    git(["worktree", "prune"], cwd=repo)


def _read_snapshot(path: Path) -> RunState | None:
    if not path.exists():
        return None
    try:
        return RunState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, KeyError):
        return None
