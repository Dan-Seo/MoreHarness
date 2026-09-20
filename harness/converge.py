"""구현 후 게이트와 ship. docs/08 이 canonical 이다.

converge 는 "구현이 끝났다" 와 "요구사항이 다 구현됐다" 를 구분한다. debt 는
converge 의 실패 조건이 아니라 ship 의 조건이다 — 차등 판정이 task 를 면제한 것을
converge 가 다시 벌하면 두 층위의 분리가 무너진다.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

from harness.analyze import fingerprint
from harness.config import HARNESS_DIR, Config
from harness.dag import load_tasks
from harness.errors import HarnessError
from harness.events import EventType
from harness.exec.verify import workspace_content
from harness.exec.workspace import COMMITTER, integration_branch, repo_scratch
from harness.git import git
from harness.models import Task, Verdict
from harness.paths import matches
from harness.policy import CommandPolicy, approval_hash, load_approvals
from harness.spec import load_specs, spec_hash
from harness.store import Store

COVERAGE = "coverage.md"
RECORD = "converge.json"
SHIP_REPORT = "ship-report.md"
_GENERATED_WORKSPACE_PATHS = frozenset(
    {
        ".harness/analyze-report.md",
        ".harness/analyze.json",
        ".harness/coverage.md",
        ".harness/converge.json",
        ".harness/ship-report.md",
    }
)
_WORKSPACE_EXCLUDES = tuple(
    sorted(
        {
            ".harness/runs",
            ".harness/runs/**",
            ".harness/knowledge",
            ".harness/knowledge/**",
            ".harness/waivers.yaml",
            *_GENERATED_WORKSPACE_PATHS,
        }
    )
)


def latest_run_id(repo: Path | str) -> str | None:
    runs = Path(repo) / HARNESS_DIR / "runs"
    if not runs.is_dir():
        return None
    ids = sorted(entry.name for entry in runs.iterdir() if entry.is_dir())
    return ids[-1] if ids else None


def _configured_store(run_dir: Path, config: Config) -> Store:
    from harness.redact import Redactor

    return Store(run_dir, redactor=Redactor(patterns=config.secret_patterns).payload)


# --------------------------------------------------------------------------- converge


@dataclass(frozen=True)
class ConvergeReport:
    ok: bool
    run_id: str
    coverage: Mapping[str, str]
    failures: tuple[str, ...]
    open_debts: tuple[str, ...]


def converge(repo: Path | str, config: Config, run_id: str | None = None) -> ConvergeReport:
    repo = Path(repo)
    run_id = run_id or latest_run_id(repo)
    if run_id is None:
        raise HarnessError("converge 할 run 이 없다")
    run_dir = repo / HARNESS_DIR / "runs" / run_id
    if not (run_dir / "journal.jsonl").is_file():
        raise HarnessError(f"run 을 찾을 수 없다: {run_id}")

    store = _configured_store(run_dir, config)
    integration = integration_branch(run_id)
    tip = _rev(repo, integration)
    freshness = _freshness(repo, config, tip, store)
    tasks = load_tasks(repo)
    specs = load_specs(repo)

    failures: list[str] = []
    coverage = _coverage(specs, tasks, store)
    for rid, status in coverage.items():
        if status != "covered":
            failures.append(f"{rid}: {status}")

    requirements = {
        requirement.get("id"): path
        for path, data in specs.items()
        for requirement in data.get("requirements") or ()
        if isinstance(requirement, dict)
    }
    for task in tasks.values():
        if not task.spec_hash or not task.satisfies:
            continue
        spec_path = next((requirements[r] for r in task.satisfies if r in requirements), None)
        if spec_path is not None and task.spec_hash != spec_hash(spec_path):
            failures.append(f"{task.id}: 드리프트 — spec_hash 가 현재 spec 과 다르다")

    if tip:
        failures += _orphans(repo, integration, tasks, store)

    workspace, cleanup = _checkout(repo, run_id, integration, tip)
    try:
        failures += _union(repo, config, tasks, store, workspace)
    finally:
        cleanup()

    if freshness != _freshness(
        repo, config, _rev(repo, integration), _configured_store(run_dir, config)
    ):
        failures.append("converge inputs changed during validation")

    report = ConvergeReport(
        not failures,
        run_id,
        coverage,
        tuple(failures),
        tuple(sorted(store.state.open_debts)),
    )
    _write_converge(repo, report, freshness)
    return report


def _coverage(specs, tasks: Mapping[str, Task], store: Store) -> dict[str, str]:
    """docs/08 의 커버리지 매트릭스 — R-### → task → verdict."""
    projections = store.state.tasks
    out: dict[str, str] = {}
    for data in specs.values():
        for requirement in data.get("requirements") or ():
            rid = requirement.get("id") if isinstance(requirement, dict) else None
            if not rid:
                continue
            owners = [task for task in tasks.values() if rid in task.satisfies]
            if not owners:
                out[rid] = "uncovered"
                continue
            verified = [
                (projection := projections.get(task.id)) is not None
                and projection.verdict is Verdict.VERIFIED
                for task in owners
            ]
            out[rid] = "covered" if all(verified) else ("partial" if any(verified) else "unverified")
    return out


def _orphans(repo: Path, integration: str, tasks: Mapping[str, Task], store: Store) -> list[str]:
    """verified task 의 allowed_paths 로 설명되지 않는 통합 브랜치 변경 (docs/08)."""
    base = git(["merge-base", "HEAD", integration], cwd=repo)
    if base.exit_code != 0:
        return []
    changed = git(
        ["diff", "--no-renames", "--name-only", "-z", base.stdout.strip(), integration],
        cwd=repo,
    )
    verified = [
        task
        for task in tasks.values()
        if (projection := store.state.tasks.get(task.id)) is not None
        and projection.verdict is Verdict.VERIFIED
    ]
    if any(not task.allowed_paths for task in verified):
        return []  # 제한 없는 task 는 모든 변경을 설명한다 (docs/05)
    out = []
    for path in changed.stdout.split("\0"):
        if path and not any(
            matches(path, pattern) for task in verified for pattern in task.allowed_paths
        ):
            out.append(f"고아 diff: {path}")
    return out


def _checkout(
    repo: Path, run_id: str, integration: str, tip: str | None
) -> tuple[Path, Callable[[], None]]:
    """AC 합집합을 실행할 트리. 통합 브랜치가 없으면(safe run) 메인 워크트리다."""
    if not tip:
        return repo, lambda: None

    path = repo_scratch(repo) / run_id / "converge"
    if path.exists():
        git(["worktree", "remove", "--force", str(path)], cwd=repo)
        shutil.rmtree(path, ignore_errors=True)
    result = git(["worktree", "add", "--detach", str(path), integration], cwd=repo)
    if result.exit_code != 0:
        raise HarnessError(f"통합 워크트리를 만들 수 없다: {result.stderr.strip()}")

    def cleanup() -> None:
        git(["worktree", "remove", "--force", str(path)], cwd=repo)
        shutil.rmtree(path, ignore_errors=True)
        git(["worktree", "prune"], cwd=repo)

    return path, cleanup


def _union(
    repo: Path, config: Config, tasks: Mapping[str, Task], store: Store, workspace: Path
) -> list[str]:
    """전체 AC 합집합 + health. red 가 open debt 면 원장의 몫, 아니면 실패다 (docs/08)."""
    policy = CommandPolicy.from_config(
        config.command_policy, load_approvals(repo / HARNESS_DIR / "approved_commands.yaml")
    )
    debt_identities = {
        debt.cmd_identity
        for debt in store.state.open_debts.values()
        if debt.cmd_identity
    }
    debt_cmds = {
        debt.cmd for debt in store.state.open_debts.values() if not debt.cmd_identity
    }

    checks: list[tuple[tuple[str, ...], bool]] = []
    for task in tasks.values():
        checks += [(criterion.cmd, criterion.shell) for criterion in task.acceptance]
    checks += [(cmd, False) for cmd in config.health_commands]

    failures, seen = [], set()
    for cmd, shell in checks:
        if (cmd, shell) in seen:
            continue
        seen.add((cmd, shell))
        result = policy.run(cmd, workspace, config.ac_timeout_s, shell=shell)
        store.append(EventType.COMMAND_POLICY_DECISION, result.decision.to_payload())
        joined = " ".join(cmd)
        if not result.decision.may_execute:
            failures.append(f"정책이 커맨드를 막았다: {joined}")
        elif result.exit_code != 0:
            if approval_hash(cmd) in debt_identities or cmd in debt_cmds:
                continue  # open debt — ship 의 조건이지 converge 의 실패가 아니다
            failures.append(f"red: {joined}")
    return failures


def _write_converge(
    repo: Path, report: ConvergeReport, freshness: Mapping[str, str | None]
) -> None:
    lines = [
        "# coverage",
        "",
        f"- run: {report.run_id}",
        f"- 결과: {'통과' if report.ok else '실패'}",
        "",
        "| R | 상태 |",
        "|---|---|",
        *[f"| {rid} | {status} |" for rid, status in report.coverage.items()],
        "",
    ]
    if report.failures:
        lines += ["## 실패", "", *[f"- {item}" for item in report.failures], ""]
    if report.open_debts:
        lines += [
            "## open_debts — ship 이 막는다",
            "",
            *[f"- {debt_id}" for debt_id in report.open_debts],
            "",
        ]
    (repo / HARNESS_DIR / COVERAGE).write_text("\n".join(lines), encoding="utf-8")
    (repo / HARNESS_DIR / RECORD).write_text(
        json.dumps(
            {
                "ok": report.ok,
                "run_id": report.run_id,
                **freshness,
                "coverage": dict(report.coverage),
                "failures": list(report.failures),
                "open_debts": list(report.open_debts),
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- ship


@dataclass(frozen=True)
class ShipReport:
    ok: bool
    run_id: str
    detail: str
    waived: tuple[str, ...] = ()


def ship(repo: Path | str, config: Config, run_id: str | None = None) -> ShipReport:
    repo = Path(repo)
    run_id = run_id or latest_run_id(repo)
    if run_id is None:
        raise HarnessError("ship 할 run 이 없다")

    integration = integration_branch(run_id)
    tip = _rev(repo, integration)
    store = _configured_store(repo / HARNESS_DIR / "runs" / run_id, config)
    current = _freshness(repo, config, tip, store)
    record = _read_json(repo / HARNESS_DIR / RECORD)
    if (
        record is None
        or record.get("run_id") != run_id
        or not record.get("ok")
        or any(record.get(key) != value for key, value in current.items())
    ):
        return _shipped(
            repo, ShipReport(False, run_id, "converge 를 먼저 실행하라 — 결과가 없거나 낡았거나 실패다")
        )

    waivers = _waivers(repo)
    waived = [debt_id for debt_id in sorted(store.state.open_debts) if debt_id in waivers]
    remaining = [debt_id for debt_id in sorted(store.state.open_debts) if debt_id not in waivers]
    if remaining:
        return _shipped(
            repo,
            ShipReport(
                False,
                run_id,
                f"open debt 가 남아 있다: {', '.join(remaining)} — "
                "waiver 없이 깨진 상태로 ship 되는 경로는 없다 (docs/08)",
                tuple(waived),
            ),
        )

    already = {
        event.payload.get("debt_id")
        for event in store.journal.read()
        if event.type is EventType.DEBT_WAIVED
    }
    for debt_id in waived:
        if debt_id in already:
            continue
        entry = waivers[debt_id]
        store.append(
            EventType.DEBT_WAIVED,
            {
                "debt_id": debt_id,
                "approver": entry.get("approver"),
                "reason": entry.get("reason"),
            },
        )

    if tip:
        merged = git([*COMMITTER, "merge", "--no-edit", integration], cwd=repo)
        if merged.exit_code != 0:
            git(["merge", "--abort"], cwd=repo)
            return _shipped(
                repo,
                ShipReport(
                    False,
                    run_id,
                    f"사용자 브랜치 머지 충돌: {(merged.stdout + merged.stderr).strip()}",
                    tuple(waived),
                ),
            )
        detail = "통합 브랜치가 사용자 브랜치로 머지되었다"
    else:
        detail = "safe run — 변경이 이미 메인 워크트리에 있으므로 머지가 없다"

    return _shipped(repo, ShipReport(True, run_id, detail, tuple(waived)))


def _shipped(repo: Path, report: ShipReport) -> ShipReport:
    lines = [
        "# ship report",
        "",
        f"- run: {report.run_id}",
        f"- 결과: {'통과' if report.ok else '실패'}",
        f"- {report.detail}",
    ]
    if report.waived:
        lines += ["", "## waived", "", *[f"- {debt_id}" for debt_id in report.waived]]
    (repo / HARNESS_DIR / SHIP_REPORT).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def _waivers(repo: Path) -> dict[str, Mapping[str, Any]]:
    path = repo / HARNESS_DIR / "waivers.yaml"
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    out = {}
    for entry in data.get("waivers") or ():
        if isinstance(entry, Mapping) and entry.get("debt_id"):
            out[str(entry["debt_id"])] = entry
    return out


def _rev(repo: Path, ref: str) -> str | None:
    result = git(["rev-parse", "--verify", "--quiet", ref], cwd=repo)
    return result.stdout.strip() if result.exit_code == 0 else None


def _freshness(
    repo: Path, config: Config, tip: str | None, store: Store
) -> dict[str, str | None]:
    return {
        "integration": tip,
        "head": _rev(repo, "HEAD"),
        "fingerprint": fingerprint(repo),
        "config_fingerprint": _config_fingerprint(config),
        "approvals_fingerprint": _file_fingerprint(
            repo / HARNESS_DIR / "approved_commands.yaml"
        ),
        "workspace_fingerprint": _workspace_fingerprint(repo) if tip is None else None,
        "projection_fingerprint": _projection_fingerprint(store),
    }


def _config_fingerprint(config: Config) -> str:
    values = asdict(config)
    values.pop("path", None)
    serialized = json.dumps(
        {
            "effective": values,
            "source": _file_fingerprint(config.path),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _file_fingerprint(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _workspace_fingerprint(repo: Path) -> str:
    content = workspace_content(repo, exclude=_WORKSPACE_EXCLUDES)
    serialized = json.dumps(
        content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _projection_fingerprint(store: Store) -> str:
    projection = {
        "tasks": {
            task_id: task.to_dict() for task_id, task in sorted(store.state.tasks.items())
        },
        "open_debts": {
            debt_id: debt.to_dict()
            for debt_id, debt in sorted(store.state.open_debts.items())
        },
    }
    serialized = json.dumps(
        projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
