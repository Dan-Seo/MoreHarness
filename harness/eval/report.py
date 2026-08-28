"""`eval.json` 과 `eval-report.md`. docs/11 이 canonical 이다.

집계만 보여주지 않는다 — 각 arm 의 실패 사례를 fixture 단위로 나열하고, repeat 가
3 미만이면 경고를 박는다. 단일 실행 수치는 개선의 근거가 아니다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from harness.eval import metrics as _metrics
from harness.eval.arms import CapabilityResult

CONTINUOUS = ("wall_time_s", "agent_time_s", "tokens_in", "tokens_out", "cost_usd", "context_tokens")
RATE_KEYS = ("grader_success_rate", "hidden_ac_pass_rate", "escape_rate", "false_block_rate")


def build(
    results: Sequence[CapabilityResult], *, repeat: int, workdir: Path | str
) -> dict[str, Any]:
    arms_payload: dict[str, Any] = {}
    for arm in dict.fromkeys(result.arm for result in results):
        subset = [result for result in results if result.arm == arm]
        run_metrics = [_metrics.of_run(result.repo, result.run_id) for result in subset]
        entry: dict[str, Any] = dict(_metrics.rates(subset))
        entry["runs"] = [_run_payload(result) for result in subset]
        entry["metrics"] = {
            name: _metrics.median_range([getattr(m, name) for m in run_metrics])
            for name in CONTINUOUS
        }
        passes = [m.first_pass for m in run_metrics if m.first_pass is not None]
        entry["first_pass_rate"] = sum(passes) / len(passes) if passes else None
        arms_payload[arm] = entry
    return {"repeat": repeat, "workdir": str(workdir), "arms": arms_payload}


def render(payload: dict[str, Any]) -> str:
    lines = ["# eval report", ""]
    if payload["repeat"] < 3:
        lines += [
            f"> **경고** — repeat {payload['repeat']} 은 3 미만이다. "
            "단일·소수 실행 수치를 개선의 근거로 제시하지 말 것 (docs/11).",
            "",
        ]
    lines += [f"- repeat: {payload['repeat']}", f"- workdir: `{payload['workdir']}`", ""]

    lines += [
        "| arm | " + " | ".join(RATE_KEYS) + " |",
        "|---|" + "---|" * len(RATE_KEYS),
    ]
    for arm, entry in payload["arms"].items():
        lines.append("| " + " | ".join([arm, *(_fmt(entry[key]) for key in RATE_KEYS)]) + " |")
    lines.append("")

    for arm, entry in payload["arms"].items():
        lines += [f"## {arm}", ""]
        for name in CONTINUOUS:
            stats = entry["metrics"][name]
            value = (
                "n/a"
                if stats is None
                else f"중앙값 {stats['median']:g} [{stats['min']:g}, {stats['max']:g}]"
            )
            lines.append(f"- {name}: {value}")
        failures = [run for run in entry["runs"] if not run["grader_success"]]
        if failures:
            lines += ["", "### 실패한 실행", ""]
            for run in failures:
                lines.append(
                    f"- {run['fixture']} ({run['run_id']}) — harness: {run['harness']}, "
                    f"hidden AC {run['hidden_ac']['green']}/{run['hidden_ac']['total']}"
                )
        lines.append("")
    return "\n".join(lines)


def write(out_dir: Path | str, payload: dict[str, Any]) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "eval.json"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    md_path = out / "eval-report.md"
    md_path.write_text(render(payload), encoding="utf-8")
    return json_path, md_path


def _run_payload(result: CapabilityResult) -> dict[str, Any]:
    return {
        "fixture": result.fixture,
        "repo": str(result.repo),
        "run_id": result.run_id,
        "harness": result.harness,
        "grader_success": result.grader.success,
        "hidden_ac": {"green": result.grader.green, "total": result.grader.total},
        "checks": [
            {
                "cmd": list(check.cmd),
                "exit_code": check.exit_code,
                "green": check.green,
                "detail": check.detail,
            }
            for check in result.grader.checks
        ],
    }


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"
