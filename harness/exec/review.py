"""리뷰 티어, bounded wave, fixer. docs/06 이 canonical 이다. 옵션 모듈이다 (docs/02).

리뷰어는 agent 지만 finding 은 하네스가 수집하는 증거다 — 리뷰어는 구현자의 대화를
보지 않고, blocking 판정은 하네스가 결정론적으로 한다. fixer 가 만든 변경도 같은
증거 기준(AC post + diff·경로 판정)을 다시 통과해야 한다.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from harness import risk as risk_module
from harness.config import HARNESS_DIR, Config
from harness.events import EventType
from harness.exec import verify as verify_module
from harness.exec.runner import AttemptOutcome, Runner
from harness.exec.workspace import Workspace
from harness.models import RiskLevel, Task, Verdict
from harness.schemas import first_error

BLOCKING_SEVERITIES = ("high", "critical")

# docs/06 의 티어 표. 아래 티어의 리뷰어를 누적한다.
REVIEWERS: dict[RiskLevel, tuple[str, ...]] = {
    RiskLevel.TRIVIAL: (),
    RiskLevel.LOW: ("spec",),
    RiskLevel.MEDIUM: ("spec", "quality"),
    RiskLevel.HIGH: ("spec", "quality", "architecture-security"),
    RiskLevel.CRITICAL: ("spec", "quality", "architecture-security", "adversarial"),
}

FOCUS = {
    "spec": "task 계약과 spec 준수 여부",
    "quality": "코드 품질 — 명확성, 중복, 에러 처리",
    "architecture-security": "아키텍처 경계와 보안 — 비밀, 입력 검증, 권한",
    "adversarial": "적대적 관점 — 이 변경이 틀렸다고 가정하고 반례를 찾는다",
}


@dataclass(frozen=True)
class Finding:
    reviewer: str
    severity: str
    rule: str
    file: str
    line: int | None
    message: str


@dataclass(frozen=True)
class Reviewed:
    """리뷰 단계의 결과. `outcome` 이 None 이면 blocking 없이 통과했다는 뜻이다."""

    outcome: AttemptOutcome | None
    evidence: verify_module.Evidence


class _BudgetExhausted(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


class ReviewStage:
    def __init__(self, repo: Path | str, config: Config) -> None:
        self.repo = Path(repo)
        self.config = config
        self.rules = risk_module.load_rules(config.risk_rules)
        self.critical_rules = _constitution_critical(self.repo)

    # ----------------------------------------------------------------- 진입점

    def run(
        self,
        runner: Runner,
        task: Task,
        attempt: int,
        workspace: Workspace,
        baseline: verify_module.Baseline,
        before: verify_module.DiffObservation,
        evidence: verify_module.Evidence,
        base: str | None = None,
    ) -> Reviewed:
        reviewers = REVIEWERS[self._tier(runner, task, attempt, evidence.diff)]
        if not reviewers:
            return Reviewed(None, evidence)

        try:
            return self._waves(
                runner, task, attempt, workspace, baseline, before, evidence, reviewers, base
            )
        except _BudgetExhausted as exhausted:
            return Reviewed(AttemptOutcome(Verdict.BUDGET_EXHAUSTED, exhausted.reason), evidence)

    def _waves(
        self,
        runner: Runner,
        task: Task,
        attempt: int,
        workspace: Workspace,
        baseline: verify_module.Baseline,
        before: verify_module.DiffObservation,
        evidence: verify_module.Evidence,
        reviewers: Sequence[str],
        base: str | None = None,
    ) -> Reviewed:
        for wave in range(1, self.config.max_review_waves + 1):
            findings = self._wave(runner, task, attempt, workspace, wave, reviewers, evidence.diff)
            if findings is None:
                # 리뷰가 성립하지 않았다 — 침묵은 통과가 아니다 (docs/06)
                return Reviewed(AttemptOutcome(Verdict.ERROR, "review_failed"), evidence)

            blocking = [finding for finding in findings if self._blocking(finding)]
            if not blocking:
                return Reviewed(None, evidence)
            if wave >= self.config.max_review_waves:
                break

            self._guard(runner)
            runner.store.append(
                EventType.FIXER_DISPATCHED, {"wave": wave, "scope": "code"}, task.id, attempt
            )
            outbox = workspace.root / "outbox" / f"attempt-{attempt}-fix-{wave}"
            runner._dispatch(
                task, attempt, workspace, self._fix_prompt(task, workspace, blocking), outbox=outbox
            )

            # 리뷰가 만든 변경도 같은 증거 기준을 통과해야 한다 (docs/06)
            differentials = runner._post(task, attempt, baseline, workspace.path)
            diff = verify_module.observe_diff(
                workspace.path, runner._harness_paths(workspace.path), base=base
            ).without(before)
            violations = verify_module.check_paths(
                diff, task.allowed_paths, runner._forbidden(task)
            )
            for violation in violations:
                runner.store.append(
                    EventType.PATH_VIOLATION, violation.to_payload(), task.id, attempt
                )
            evidence = verify_module.judge(task, diff, differentials, violations)
            if evidence.verdict is not None or evidence.next_state is not None:
                return Reviewed(
                    AttemptOutcome(evidence.verdict, evidence.reason, evidence.next_state),
                    evidence,
                )

        # wave 한도 초과 후에도 blocking 잔존 → rejected (docs/10 의 review 행)
        return Reviewed(AttemptOutcome(Verdict.REJECTED, "blocking_findings"), evidence)

    # ----------------------------------------------------------------- 티어

    def _tier(
        self, runner: Runner, task: Task, attempt: int, diff: verify_module.DiffObservation
    ) -> RiskLevel:
        """사후 단계 — 실제 diff 를 본 뒤 확정한다. 상향은 risk_escalated 로 남는다."""
        floors = risk_module.assess(
            task.risk,
            (*diff.changed_files, *diff.created_files),
            diff.diff_stat.get("insertions", 0) + diff.diff_stat.get("deletions", 0),
            len(diff.changed_files) + len(diff.created_files),
            self.rules,
        )
        if floors.escalated:
            runner.store.append(EventType.RISK_ESCALATED, floors.to_payload(), task.id, attempt)
        return floors.effective

    # ----------------------------------------------------------------- wave

    def _wave(
        self,
        runner: Runner,
        task: Task,
        attempt: int,
        workspace: Workspace,
        wave: int,
        reviewers: Sequence[str],
        diff: verify_module.DiffObservation,
    ) -> list[Finding] | None:
        review_dir = runner.store.run_dir / "tasks" / task.id / "review" / f"wave-{wave}"
        review_dir.mkdir(parents=True, exist_ok=True)

        merged: dict[tuple, Finding] = {}
        for name in reviewers:
            self._guard(runner)
            outbox = workspace.root / "outbox" / f"attempt-{attempt}-review-{wave}-{name}"
            runner._dispatch(
                task,
                attempt,
                workspace,
                self._review_prompt(task, workspace, name, diff),
                outbox=outbox,
            )
            findings = self._normalize(task, outbox / "findings.json", review_dir, name)
            if findings is None:
                return None
            for finding in findings:
                key = (finding.rule, finding.file, finding.line)  # docs/06 의 중복 제거 키
                if key in merged:
                    continue
                merged[key] = finding
                runner.store.append(
                    EventType.REVIEW_FINDING,
                    {
                        "wave": wave,
                        "reviewer": finding.reviewer,
                        "severity": finding.severity,
                        "rule": finding.rule,
                        "file": finding.file,
                        "line": finding.line,
                        "blocking": self._blocking(finding),
                    },
                    task.id,
                    attempt,
                )
        return list(merged.values())

    def _normalize(
        self, task: Task, raw_path: Path, review_dir: Path, name: str
    ) -> list[Finding] | None:
        """findings 파일을 보존하고 검증한다. 없거나 깨졌으면 리뷰 불성립이다."""
        if not raw_path.is_file():
            return None
        try:
            data = json.loads(raw_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if data is None or first_error("findings", data):
            shutil.copyfile(raw_path, review_dir / f"{name}.invalid.json")
            return None
        shutil.copyfile(raw_path, review_dir / f"{name}.json")
        return [
            Finding(
                reviewer=name,
                severity=entry["severity"],
                rule=entry["rule"],
                file=entry["file"],
                line=entry.get("line"),
                message=entry["message"],
            )
            for entry in data["findings"]
        ]

    def _blocking(self, finding: Finding) -> bool:
        """결정론적이다 — 리뷰어가 스스로 blocking 을 정하지 않는다 (docs/06)."""
        return finding.severity in BLOCKING_SEVERITIES or finding.rule in self.critical_rules

    def _guard(self, runner: Runner) -> None:
        spent = runner._over_budget()
        if spent:
            raise _BudgetExhausted(spent)

    # ----------------------------------------------------------------- 프롬프트

    def _review_prompt(
        self, task: Task, workspace: Workspace, name: str, diff: verify_module.DiffObservation
    ) -> str:
        """fresh context — constitution + task 계약 + 변경 파일뿐이다."""
        parts = [
            _read(self.repo / HARNESS_DIR / "constitution.md"),
            f"## 독립 리뷰 — {name}\n\n"
            f"당신은 이 변경의 구현자가 아니다. 관점: {FOCUS[name]}\n"
            "구현자의 서사는 제공되지 않는다 — 코드만 본다.",
            _card(task),
            _changed_files(workspace, diff),
            "## 산출물\n\n"
            "`$HARNESS_OUTBOX/findings.json` 에 다음 형식으로 쓴다. 발견이 없으면 "
            "`findings` 를 빈 배열로 쓴다.\n"
            '`{"schema": "harness.findings/v1", "task_id": "' + task.id + '", '
            '"findings": [{"severity": "...", "rule": "...", "file": "...", '
            '"line": 0, "message": "..."}]}`',
        ]
        return "\n\n".join(part for part in parts if part) + "\n"

    def _fix_prompt(self, task: Task, workspace: Workspace, blocking: Sequence[Finding]) -> str:
        lines = [
            f"- [{finding.severity}] {finding.rule} {finding.file}"
            + (f":{finding.line}" if finding.line else "")
            + f" — {finding.message}"
            for finding in blocking
        ]
        return (
            f"## Fixer — {task.id} 의 blocking findings 를 해소한다\n\n"
            "워크스페이스의 코드를 직접 고친다. allowed_paths 밖을 건드리지 말 것.\n\n"
            + "\n".join(lines)
            + "\n\n"
            + _card(task)
            + "\n"
        )


def _constitution_critical(repo: Path) -> frozenset[str]:
    """docs/06 — constitution 의 `## critical` 섹션 리스트 항목이 blocking rule 목록이다."""
    path = repo / HARNESS_DIR / "constitution.md"
    if not path.is_file():
        return frozenset()
    rules, inside = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^#{1,6}\s+(.*?)\s*$", line)
        if heading:
            inside = heading.group(1).strip().lower() == "critical"
            continue
        if inside:
            item = re.match(r"^[-*]\s+`?([\w.-]+)`?", line.strip())
            if item:
                rules.append(item.group(1))
    return frozenset(rules)


def _card(task: Task) -> str:
    lines = [f"## Task 계약\n- id: {task.id}", f"- kind: {task.kind}"]
    if task.allowed_paths:
        lines.append(f"- allowed_paths: {', '.join(task.allowed_paths)}")
    if task.acceptance:
        lines.append("- acceptance:")
        lines += [f"    {' '.join(criterion.cmd)}" for criterion in task.acceptance]
    return "\n".join(lines)


def _changed_files(workspace: Workspace, diff: verify_module.DiffObservation) -> str:
    parts = []
    for relative in (*diff.changed_files, *diff.created_files):
        body = _read(workspace.path / relative)
        if body:
            parts.append(f"### {relative}\n\n```\n{body}```")
    return "## 변경된 파일\n\n" + "\n\n".join(parts) if parts else ""


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else ""
    except (OSError, UnicodeDecodeError):
        return ""
