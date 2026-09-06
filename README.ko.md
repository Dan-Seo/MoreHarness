# Harness Framework

*이 문서는 [`README.md`](README.md)의 번역이다. 둘이 어긋나면 영문이 canonical이다.*

> **Agents propose. Harness verifies. Evidence decides.**

코딩 agent를 실행하고, **그 결과가 진짜인지 하네스가 직접 확인하는** 프레임워크.

agent는 코드를 쓰고 자기가 무엇을 했는지 보고한다. 하네스는 그 보고를 판정 근거로 쓰지
않는다. acceptance criteria를 직접 실행하고, git diff를 직접 읽고, 변경 경로를 직접
판정한다. 왜 그렇게 만들었는지와 파이프라인 전체는 [`docs/00-OVERVIEW.md`](docs/ko/00-OVERVIEW.md)에 있다.

## 상태

`0.2.0`. 마일스톤 M0~M9가 전부 구현되어 있고 오프라인 테스트로 검증한다. 옵션 레이어를 전부
제거해도 커널이 동작한다는 것은 `tests/test_kernel_only.py`가 서브프로세스로 강제한다.

기록된 능력 eval의 범위는 `claude` CLI, fixture 하나(`evals/capability/slugify`),
`worktree` 프로파일이다. Codex에는 어댑터 계약 테스트와 아래 설명하는 선택 실행 방식의
실제 CLI 스모크 테스트가 있다. 스모크 테스트는 능력 벤치마크가 아니다.
[기록된 Codex 스모크 테스트](evals/codex-smoke-report.md)는 Windows의 Codex CLI
0.153.4에서 통과했다.

## 요구사항

- Python 3.11+
- git
- 런타임 의존성: `PyYAML`, `jsonschema` (그 외는 stdlib)
- agent를 실제로 돌리려면 설치 및 인증된 벤더 CLI(`claude` 또는 `codex`). 어댑터가 서브프로세스로만 호출한다.

## 설치

PyPI 에 없다. 클론해서 설치한다.

```bash
git clone https://github.com/Dan-Seo/MoreHarness.git
cd MoreHarness
pip install .
```

`harness` 명령이 설치된다. 그다음부터는 이 저장소가 아니라 **작업할 프로젝트 저장소** 안에서
실행한다. 설치 없이 쓰려면 클론 루트에서 `python -m harness`도 같다.

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

Codex를 사용하려면 어댑터 설정을 다음과 같이 바꾼다.

```yaml
defaults:
  adapter: codex

adapters:
  codex:
    type: codex_cli
    binary: codex
    extra_args: ["--sandbox", "workspace-write"]

agent_timeout_s: 900
```

`harness init`이 만든 나머지 설정은 유지한다. 먼저 `codex --version`과
`codex login status`를 확인한 다음, 대상 프로젝트에서 `harness doctor`로 어댑터 탐색을
확인한다. Windows에서 CLI가 PATH에 없으면 `binary`에 네이티브 `codex.exe`의 절대 경로를
지정할 수 있다. 어댑터는 프롬프트를 stdin으로 주고 현재 outbox를 `--add-dir`로 추가한다.
계약은 [`docs/04-AGENT-ADAPTER.md`](docs/ko/04-AGENT-ADAPTER.md#codex_cli)에 있다.
코드 수정을 허용하려면 `workspace-write` 설정이 필요하다. Codex 비대화형 실행의 기본값은
read-only다. [공식 Codex 안내](https://learn.chatgpt.com/docs/non-interactive-mode)를 참고한다.

같은 파일의 `command_policy`가 **하네스가 실행하는 모든 커맨드**를 통과시킨다. 기본값은
fail-closed이고, 무엇이 자동 실행 승인되었는지는 거기 보이는 것이 전부다. 쓰기 전에 읽고
프로젝트에 맞게 고친다.

## TDD 강제 모드

task 가 `development.mode: tdd` 를 선언하면 하네스가 red→green 순서를 **직접 관측한다.**
"TDD 로 했다"는 agent 의 보고는 증거가 아니다.

```yaml
# tasks/T-003.task.yaml
acceptance:
  - cmd: ["python", "-m", "pytest", "-q", "tests/api"]
    expect_fail_before: true      # red gate 가 이 커맨드로 red 를 확인한다

development:
  mode: tdd
  test_paths:           ["tests/api/**"]
  implementation_paths: ["src/api/**"]
```

한 attempt 가 두 번 디스패치된다.

```
test-author  →  하네스가 diff 를 본다 (구현 경로를 건드리면 rejected)
             →  테스트를 커밋해 관측 기준을 고정한다
red gate     →  하네스가 AC 를 직접 실행한다. green 이면 구현을 디스패치하지 않는다
implementation → 하네스가 red gate 커밋 대비 diff 를 본다
             →  테스트를 고치거나 지우면 커밋해서 감춰도 탐지된다
green gate   →  기존 차등 판정 — red→green 과 회귀를 함께 본다
```

기존 verified 조건이 약해지지 않는다. TDD 는 증거를 더할 뿐이다. 계약은
[`docs/06-VERIFICATION-REVIEW.md`](docs/ko/06-VERIFICATION-REVIEW.md)가 canonical 이다.

## 무엇을 보증하지 않는가

읽고 시작할 것 — 이 프레임워크는 보증 범위를 명시적으로 좁혀 놓았다.

- 실행 프로파일이 무엇을 보장하고 무엇을 보장하지 않는지: [`docs/05-EXECUTION-ISOLATION.md`](docs/ko/05-EXECUTION-ISOLATION.md)
- 위협 모델과 비보장 목록: [`docs/09-SECURITY.md`](docs/ko/09-SECURITY.md)

요약하지 않는다. 두 문서가 canonical이다.

## 측정

개선은 journal의 projection으로만 측정한다. 지표 정의는 [`docs/11-EVALUATION.md`](docs/ko/11-EVALUATION.md),
실제로 돌린 첫 능력 eval 결과는 [`evals/capability/eval-report.md`](evals/capability/eval-report.md)에 있다.

```
harness eval run --fixtures evals/capability --arms raw,harness-full --repeat 3
```

## 문서

설계 문서 13개가 `docs/`에 있고 한 개념의 canonical 정의는 정확히 한 문서에만 있다.
[`docs/00-OVERVIEW.md`](docs/ko/00-OVERVIEW.md)의 문서 지도에서 시작한다.

## 개발

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```

기여 전에 [`AGENTS.md`](AGENTS.md)를 읽는다. Codex와 Claude Code가 함께 쓰는 설치,
아키텍처, 검증 지침이며 [`CLAUDE.md`](CLAUDE.md)에서 불러온다. 계약을 바꾸려면
canonical 문서를 먼저 고치고 테스트를 먼저 쓴다.

오프라인 테스트에서는 실제 Codex 테스트를 건너뛴다. 인증된 CLI로 실행하려면 다음 명령을
쓴다. 이 테스트는 모델 사용량을 소비한다.

```bash
python -m pytest tests/test_codex_live.py -q -s --codex-binary codex
```

PowerShell에서는 네이티브 실행 파일 경로를 직접 지정할 수 있다.

```powershell
python -m pytest tests/test_codex_live.py -q -s --codex-binary 'C:/path/to/codex.exe'
```

테스트는 임시 Git 저장소를 만들고 Codex가 그 저장소의 `AGENTS.md`를 따르는지,
허용된 파일만 수정하는지, 외부 outbox에 유효한 claim과 handoff를 쓰는지 확인한다.
하네스가 직접 실행한 acceptance의 red→green 결과로 `verified`를 받는지도 검사한다.
시도는 한 번이며 agent 타임아웃은 180초다. 실행 기록은
`<system temp>/harness-codex-smoke-<id>/`에 남고 journal과 transcript를 확인할 수 있도록
run 경로를 출력한다. Windows 네이티브 sandbox가 접근할 수 있도록 pytest 전용 비공개
임시 폴더 대신 일반 시스템 임시 폴더를 사용한다.
이 체크아웃의 브랜치와 기존 능력 eval 기록은 바꾸지 않는다.

## 라이선스

MIT. 전문은 [`LICENSE`](LICENSE)에 있다.
