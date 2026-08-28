"""저작 단계 — spec/clarify/plan/tasks 의 스캐폴드와 `spec_hash`. docs/08 이 canonical 이다.

산출물의 내용은 사람(또는 사람이 시킨 agent)이 채운다. 여기서는 형태만 만들고,
정합성은 analyze 가 본다. 이미 있는 파일은 덮어쓰지 않는다.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from harness.dag import load_tasks
from harness.errors import HarnessError

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
NEEDS = "[NEEDS CLARIFICATION]"


def slugify(intent: str) -> str:
    cleaned = re.sub(r"[^\w\- ]", "", intent.strip().lower())
    slug = re.sub(r"\s+", "-", cleaned)
    if not slug:
        raise HarnessError(f"intent 에서 slug 를 만들 수 없다: {intent!r}")
    return slug


def spec_hash(path: Path) -> str:
    """docs/03 — spec 파일 바이트의 sha256, `sha256:` 접두사."""
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load_specs(repo: Path | str) -> dict[Path, dict]:
    """`specs/*/spec.yaml` 전부. yaml 오류는 호출자가 분류한다."""
    specs: dict[Path, dict] = {}
    for path in sorted(Path(repo).glob("specs/*/spec.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            specs[path] = data
    return specs


# --------------------------------------------------------------------------- spec


def create_spec(repo: Path | str, intent: str, slug: str | None = None) -> Path:
    slug = slug or slugify(intent)
    path = Path(repo) / "specs" / slug / "spec.yaml"
    if path.exists():
        raise HarnessError(f"{path} 이(가) 이미 있다 — 덮어쓰지 않는다")
    template = (TEMPLATE_DIR / "spec.yaml").read_text(encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template.format(slug=slug, intent=intent), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- clarify


def open_questions(repo: Path | str) -> list[tuple[Path, str]]:
    """`open_questions` 중 marker 가 남은 항목. analyze 는 spec 텍스트 전체를 본다 (docs/08)."""
    found = []
    for path, data in load_specs(repo).items():
        for question in data.get("open_questions") or ():
            if isinstance(question, str) and NEEDS in question:
                found.append((path, question))
    return found


def resolve_question(path: Path, question: str, answer: str) -> None:
    """항목을 `clarifications` 로 옮긴다. yaml 재직렬화이므로 주석은 잃는다."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["open_questions"] = [
        entry for entry in data.get("open_questions") or [] if entry != question
    ]
    data.setdefault("clarifications", []).append(
        {"question": question.replace(NEEDS, "").strip(), "answer": answer}
    )
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


# --------------------------------------------------------------------------- plan


def create_plans(repo: Path | str) -> list[Path]:
    created = []
    template = (TEMPLATE_DIR / "plan.md").read_text(encoding="utf-8")
    for path, data in load_specs(repo).items():
        target = path.parent / "plan.md"
        if target.exists():
            continue
        sections = "\n".join(
            f"### {requirement.get('id')} — {requirement.get('statement', '')}\n"
            for requirement in data.get("requirements") or ()
            if isinstance(requirement, dict)
        )
        target.write_text(
            template.format(slug=data.get("slug", path.parent.name), sections=sections),
            encoding="utf-8",
        )
        created.append(target)
    return created


# --------------------------------------------------------------------------- tasks


def scaffold_tasks(repo: Path | str) -> list[Path]:
    """어떤 task 도 만족시키지 않는 R-### 마다 골격 하나. `spec_hash` 는 여기서 찍는다."""
    repo = Path(repo)
    tasks = load_tasks(repo)
    satisfied = {rid for task in tasks.values() for rid in task.satisfies}
    numbers = [
        int(match.group(1))
        for task_id in tasks
        if (match := re.match(r"T-(\d+)$", task_id))
    ]
    next_number = max(numbers, default=0) + 1

    template = (TEMPLATE_DIR / "task.yaml").read_text(encoding="utf-8")
    created = []
    for path, data in load_specs(repo).items():
        digest = spec_hash(path)
        for requirement in data.get("requirements") or ():
            rid = requirement.get("id") if isinstance(requirement, dict) else None
            if not rid or rid in satisfied:
                continue
            task_id = f"T-{next_number:03d}"
            next_number += 1
            target = repo / "tasks" / f"{task_id}.task.yaml"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                template.format(
                    id=task_id, name=rid.lower(), requirement=rid, spec_hash=digest
                ),
                encoding="utf-8",
            )
            created.append(target)
    return created
