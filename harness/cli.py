"""진입점.

docs/02 — `sys.exit` 는 이 파일에만 존재한다. 다른 모듈은 예외를 올리거나 값을 반환한다.
M0 이 갖는 커맨드는 `status` 와 `doctor` 둘이다 (docs/12).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from harness.adapters.registry import build
from harness.config import load
from harness.errors import AdapterNotFoundError, ConfigError, JournalCorruptionError, NotARepositoryError
from harness.git import is_repo, repo_root
from harness.models import RunState
from harness.store import Journal, Store, check_sequence, fold

HARNESS_DIR = ".harness"
REQUIRED_CONTROL_PLANE = (
    "config.yaml",
    "constitution.md",
    "approved_commands.yaml",
    "knowledge",
    "runs",
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse 의 종료를 종료 코드로 바꾼다
        return int(exc.code or 2)

    if args.command == "status":
        return _status(args)
    return _doctor(args)


def run_cli() -> None:
    sys.exit(main(sys.argv[1:]))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness", description="Agents propose. Harness verifies.")
    sub = parser.add_subparsers(dest="command", required=True)

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


# --------------------------------------------------------------------------- status


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
        report.bad(".harness/ 구조", f"{harness_dir} 가 없다")
    else:
        missing = [name for name in REQUIRED_CONTROL_PLANE if not (harness_dir / name).exists()]
        if missing:
            report.bad(".harness/ 구조", "없음: " + ", ".join(missing))
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


def _read_snapshot(path: Path) -> RunState | None:
    if not path.exists():
        return None
    try:
        return RunState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, KeyError):
        return None
