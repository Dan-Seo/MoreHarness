"""SWE-bench 인스턴스 → eval fixture. docs/11 의 「외부 벤치마크」가 canonical 이다.

**컨버터는 변환기이지 벤치마크 실행기가 아니다.** 네트워크를 쓰지 않고 파이썬 환경도
준비하지 않는다. 로컬 체크아웃에서 `base_commit` 의 트리를 꺼내 fixture 배치로 옮기고,
숨겨야 할 것(테스트 패치)을 `grader/` 에 두는 것이 전부다.

이 모듈은 옵션이며 커널 밖이다 (docs/02).
"""

from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

import yaml

from harness.errors import HarnessError
from harness.eval.fixtures import GRADER_PLACEHOLDER
from harness.git import git

BENCHMARK = "swebench"
PYTEST = ("python", "-m", "pytest", "-q")

# fixture 가 자기 어댑터와 Command Policy 를 선언하는 자리다 (docs/11). 기본값을 써 두고
# 사람이 고쳐서 쓴다. 정규식을 담으므로 raw 문자열이다.
SEED_CONFIG = r"""# 이 fixture 의 control-plane. 컨버터가 만든 기본값이므로 고쳐서 쓴다.
# 키의 canonical 정의는 docs/03 의 `config.yaml — canonical` 이다.
version: 1

defaults:
  adapter: claude_cli
  profile: worktree
  max_parallel: 1

adapters:
  claude_cli:
    type: claude_cli

# 능력 eval 은 무인 실행이다. 여기 보이는 것만 자동 실행된다 (docs/06).
command_policy:
  default: require_approval
  rules:
    - {match: '^(pytest|python -m pytest)', verdict: allow}
    - {match: '^git apply ', verdict: allow}          # hidden grader 의 테스트 패치
    - {match: '^git (status|diff|log)', verdict: allow}
    - {match: 'rm\s+-rf|git\s+push\s+--force|git\s+reset\s+--hard|^sudo|id_rsa', verdict: deny}
"""

SEED_CONSTITUTION = """\
# Constitution

이 저장소가 절대 어기지 않는 규칙 (docs/01).

- 기존 테스트를 지우거나 통과하도록 고치지 않는다.
"""

SEED_APPROVED = """\
# require_approval 커맨드의 승인 기록 (docs/06).
approvals: []
"""


@dataclass(frozen=True)
class ImportReport:
    created: tuple[str, ...]
    skipped: tuple[tuple[str, str], ...]  # (instance_id, 사유)


class _Skip(Exception):
    """이 instance 만 건너뛴다. 하나가 없다고 전체 변환이 실패하지 않는다 (docs/11)."""


def convert(instances: Path | str, repos: Path | str, out: Path | str) -> ImportReport:
    instances, repos, out = Path(instances), Path(repos), Path(out)
    if not instances.is_file():
        raise HarnessError(f"인스턴스 파일이 없다: {instances}")

    created: list[str] = []
    skipped: list[tuple[str, str]] = []
    for record in _records(instances):
        instance_id = str(record.get("instance_id") or "").strip()
        if not instance_id:
            skipped.append(("<이름 없음>", "instance_id 가 없다"))
            continue
        try:
            _convert_one(record, instance_id, repos, out / instance_id)
        except _Skip as skip:
            skipped.append((instance_id, str(skip)))
            continue
        created.append(instance_id)
    return ImportReport(tuple(created), tuple(skipped))


def _convert_one(record: dict, instance_id: str, repos: Path, case: Path) -> None:
    # 아무것도 만들기 전에 전부 검사한다 — 건너뛴 instance 가 반쯤 만들어진 케이스를
    # 남기지 않아야 한다.
    repo = str(record.get("repo") or "")
    base_commit = str(record.get("base_commit") or "")
    checkout = _checkout(repos, repo)
    if checkout is None:
        raise _Skip(f"{repo or '<없음>'} 의 로컬 체크아웃이 {repos} 에 없다")
    if not base_commit or not _has_commit(checkout, base_commit):
        raise _Skip(f"{base_commit or '<없음>'} 커밋이 {checkout} 에 없다")

    fail_to_pass = _tests(record.get("FAIL_TO_PASS"))
    pass_to_pass = _tests(record.get("PASS_TO_PASS"))
    if not fail_to_pass and not pass_to_pass:
        raise _Skip("FAIL_TO_PASS 와 PASS_TO_PASS 가 둘 다 비어 있다 — 채점 기준이 없다")

    shutil.rmtree(case, ignore_errors=True)  # 멱등 — 이미 있는 케이스는 통째로 다시 쓴다
    _extract_tree(checkout, base_commit, case / "seed")
    _write_control_plane(case / "seed" / ".harness")
    _write_spec(case / "seed" / "specs" / instance_id, instance_id, repo, record)
    _write_task(case / "tasks")
    _write_grader(case / "grader", record, fail_to_pass, pass_to_pass)
    _write_meta(case / "meta.yaml", instance_id, repo, record, fail_to_pass, pass_to_pass)


# ------------------------------------------------------------------ 로컬 체크아웃


def _checkout(repos: Path, repo: str) -> Path | None:
    """`<owner>__<name>` 과 `<owner>/<name>` 두 배치를 모두 받는다 (docs/11)."""
    if not repo:
        return None
    for candidate in (repos / repo.replace("/", "__"), repos / repo):
        if (candidate / ".git").exists():
            return candidate
    return None


def _has_commit(checkout: Path, sha: str) -> bool:
    return git(["cat-file", "-e", f"{sha}^{{commit}}"], cwd=checkout).exit_code == 0


def _extract_tree(checkout: Path, sha: str, dest: Path) -> None:
    """`base_commit` 의 트리만 꺼낸다. `.git` 은 따라오지 않는다 — materialize 가 새로
    `git init` 하기 때문이다 (docs/11)."""
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "tree.tar"
        result = git(["archive", "--format=tar", "-o", str(archive), sha], cwd=checkout)
        if result.exit_code != 0:
            raise HarnessError(f"{sha} 의 트리를 꺼낼 수 없다: {result.stderr.strip()}")
        with tarfile.open(archive) as tar:
            tar.extractall(dest, filter="data")


# ------------------------------------------------------------------ fixture 쓰기


def _write_control_plane(harness_dir: Path) -> None:
    (harness_dir / "knowledge").mkdir(parents=True, exist_ok=True)
    (harness_dir / "config.yaml").write_text(SEED_CONFIG, encoding="utf-8")
    (harness_dir / "constitution.md").write_text(SEED_CONSTITUTION, encoding="utf-8")
    (harness_dir / "approved_commands.yaml").write_text(SEED_APPROVED, encoding="utf-8")


def _write_spec(directory: Path, instance_id: str, repo: str, record: dict) -> None:
    """문제 서술이 R-001 이다. 요구사항이 하나라는 것이 이 벤치마크의 모양이다."""
    _dump(
        directory / "spec.yaml",
        {
            "slug": instance_id,
            "intent": f"{repo} 의 이슈를 해결한다",
            "requirements": [
                {"id": "R-001", "statement": str(record.get("problem_statement") or "")}
            ],
        },
    )


def _write_task(directory: Path) -> None:
    """**보이는 AC 는 없다** (docs/11). 채점 기준을 숨기는 것이 이 벤치마크의 본질이므로
    하네스가 가진 증거는 diff 와 리뷰뿐이다."""
    _dump(
        directory / "T-001.task.yaml",
        {
            "id": "T-001",
            "name": "resolve-issue",
            "kind": "implementation",
            "satisfies": ["R-001"],
        },
    )


def _write_grader(
    directory: Path, record: dict, fail_to_pass: Sequence[str], pass_to_pass: Sequence[str]
) -> None:
    """테스트 패치는 `grader/` 에 있고 채점 시점에만 적용된다 — hidden grader 의 절대 규칙과
    같은 규칙이다 (docs/11)."""
    directory.mkdir(parents=True, exist_ok=True)
    acceptance: list[dict[str, Any]] = []

    test_patch = str(record.get("test_patch") or "")
    if test_patch:
        (directory / "test_patch.diff").write_text(test_patch, encoding="utf-8")
        acceptance.append({"cmd": ["git", "apply", f"{GRADER_PLACEHOLDER}/test_patch.diff"]})
    for tests in (fail_to_pass, pass_to_pass):
        if tests:
            acceptance.append({"cmd": [*PYTEST, *tests]})

    _dump(directory / "hidden_ac.yaml", {"acceptance": acceptance})


def _write_meta(
    path: Path,
    instance_id: str,
    repo: str,
    record: dict,
    fail_to_pass: Sequence[str],
    pass_to_pass: Sequence[str],
) -> None:
    _dump(
        path,
        {
            "benchmark": BENCHMARK,
            "instance_id": instance_id,
            "repo": repo,
            "base_commit": str(record.get("base_commit") or ""),
            "environment_setup_commit": record.get("environment_setup_commit"),
            "version": record.get("version"),
            "created_at": record.get("created_at"),
            "difficulty": "unknown",
            "domain": repo,
            "asserts": (
                f"FAIL_TO_PASS {len(fail_to_pass)} 개가 통과하고 "
                f"PASS_TO_PASS {len(pass_to_pass)} 개가 깨지지 않는다"
            ),
        },
    )


# ------------------------------------------------------------------ 읽기


def _records(path: Path) -> Iterator[dict]:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            yield json.loads(line)


def _tests(raw: Any) -> list[str]:
    """SWE-bench 는 테스트 목록을 JSON 문자열로도 리스트로도 준다."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else []
        except ValueError:
            raise _Skip(f"테스트 목록을 읽을 수 없다: {raw[:80]!r}") from None
    return [str(item) for item in raw or ()]


def _dump(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
