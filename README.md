# Harness Framework

> **Agents propose. Harness verifies. Evidence decides.**

코딩 agent를 실행하고, **그 결과가 진짜인지 하네스가 직접 확인하는** 프레임워크.

agent는 코드를 쓰고 자기가 무엇을 했는지 보고한다. 하네스는 그 보고를 판정 근거로 쓰지
않는다. acceptance criteria를 직접 실행하고, git diff를 직접 읽고, 변경 경로를 직접
판정한다. 왜 그렇게 만들었는지와 파이프라인 전체는 [`docs/00-OVERVIEW.md`](docs/00-OVERVIEW.md)에 있다.

## 상태

`0.1.0`. 마일스톤 M0~M8이 전부 구현되어 있고 테스트 595개가 통과한다. 옵션 레이어를 전부
제거해도 커널이 동작한다는 것은 `tests/test_kernel_only.py`가 서브프로세스로 강제한다.

실사용으로 확인된 범위는 아직 좁다 — 어댑터는 `claude` CLI 하나, 능력 eval fixture는 하나
(`evals/capability/slugify`), 프로파일은 `worktree` 하나다. 그 밖은 테스트로만 검증되어 있다.

## 요구사항

- Python 3.11+
- git
- 런타임 의존성: `PyYAML`, `jsonschema` (그 외는 stdlib)
- agent를 실제로 돌리려면 그 벤더의 CLI (예: `claude`). 어댑터가 서브프로세스로만 호출한다.

## 설치

```
pip install .
```

`harness` 명령이 설치된다. 설치 없이 저장소에서 바로 쓰려면 `python -m harness`도 같다.

## 5분 quickstart

```bash
cd <your-repo>            # git 저장소여야 한다
harness init              # .harness/ 생성 (멱등)
harness spec "사용자 이름을 slug 로 바꾸는 함수"   # R-### 이 붙은 Spec 골격
harness plan              # Spec → Plan
harness tasks             # Plan → tasks/*.task.yaml (DAG)
                          # ← 여기서 사람이 spec 의 [NEEDS CLARIFICATION] 과 task 의 AC 를 채운다
harness analyze           # 구현 전 게이트. 비어 있으면 여기서 막힌다
harness run               # DAG 실행
harness status            # verdict, open_debts, human_required
harness converge          # 구현 후 게이트 — 커버리지·드리프트·debt
harness ship              # 통합 브랜치를 사용자 브랜치로 머지
```

`spec`·`plan`·`tasks`는 골격만 만든다. 사람이 채운 뒤 `analyze`를 통과시켜야 `run`이 돈다.

`init`이 만드는 `.harness/config.yaml`의 기본 어댑터는 `mock`이다. 실제 agent를 붙이려면
그 자리를 바꾼다.

```yaml
defaults:
  adapter: claude

adapters:
  claude:
    type: claude_cli
    extra_args: ["--permission-mode", "acceptEdits", "--max-turns", "30"]

agent_timeout_s: 900
```

같은 파일의 `command_policy`가 **하네스가 실행하는 모든 커맨드**를 통과시킨다. 기본값은
fail-closed이고, 무엇이 자동 실행 승인되었는지는 거기 보이는 것이 전부다. 쓰기 전에 읽고
프로젝트에 맞게 고친다.

## 무엇을 보증하지 않는가

읽고 시작할 것 — 이 프레임워크는 보증 범위를 명시적으로 좁혀 놓았다.

- 실행 프로파일이 무엇을 보장하고 무엇을 보장하지 않는지: [`docs/05-EXECUTION-ISOLATION.md`](docs/05-EXECUTION-ISOLATION.md)
- 위협 모델과 비보장 목록: [`docs/09-SECURITY.md`](docs/09-SECURITY.md)

요약하지 않는다. 두 문서가 canonical이다.

## 측정

개선은 journal의 projection으로만 측정한다. 지표 정의는 [`docs/11-EVALUATION.md`](docs/11-EVALUATION.md),
실제로 돌린 첫 능력 eval 결과는 [`evals/capability/eval-report.md`](evals/capability/eval-report.md)에 있다.

```
harness eval run --fixtures evals/capability --arms raw,harness-full --repeat 3
```

## 문서

설계 문서 13개가 `docs/`에 있고 한 개념의 canonical 정의는 정확히 한 문서에만 있다.
[`docs/00-OVERVIEW.md`](docs/00-OVERVIEW.md)의 문서 지도에서 시작한다.

## 개발

```
pytest
```

기여 전에 [`CLAUDE.md`](CLAUDE.md)를 읽는다 — 계약을 바꾸려면 canonical 문서를 먼저 고치고,
테스트를 먼저 쓴다.

## 라이선스

아직 정하지 않았다. 라이선스가 붙기 전까지 저작권은 저자에게 있으며 사용 조건이 정해져
있지 않다.
