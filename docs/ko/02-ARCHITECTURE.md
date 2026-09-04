# 02 · 아키텍처

*이 문서는 [`docs/02-ARCHITECTURE.md`](../02-ARCHITECTURE.md)의 번역이다. 둘이 어긋나면 영문이 canonical이다.*

## 모듈 배치

```
harness/
  cli.py              # 진입점. sys.exit 는 오직 여기서만 호출한다.
  config.py           # .harness/config.yaml 로드·검증
  models.py           # 순수 데이터 타입. 다른 harness 모듈을 import 하지 않는다.
  events.py           # 이벤트 타입과 페이로드 스키마
  schemas.py          # resources/schemas/ 의 jsonschema 정의를 읽어 검증기를 만든다
  store.py            # journal append + state projection + 재구성
  dag.py              # 의존 그래프, 위상 정렬, ready-set
  git.py              # git 호출 래퍼 (diff, worktree, branch, merge)
  risk.py             # effective_risk 계산
  probes.py           # precondition 검사, environment verifier
  policy.py           # Command Policy — allow / deny / require_approval
  errors.py           # 예외 타입. 아무것도 import 하지 않는다. 분류는 10 의 표를 따른다.
  paths.py            # docs/05 glob 방언의 경로 매칭. 아무것도 import 하지 않는다.
  learn.py            # knowledge card 승격/폐기
  spec.py             # 저작 단계 스캐폴드 — spec/clarify/plan/tasks (08)
  analyze.py          # 구현 전 게이트 (08)
  converge.py         # 구현 후 게이트와 ship (08)

  adapters/
    base.py           # AgentAdapter 프로토콜, AgentRequest/AgentResult
    registry.py       # 이름 → 어댑터 해석
    conformance.py    # 모든 어댑터가 통과해야 하는 테스트 스위트
    mock.py           # 결정론적. 회귀 eval 과 CI 의 기본값
    generic_cli.py    # 설정만으로 임의 CLI 구동
    claude_cli.py     # 선택적 능력 추가
    codex_cli.py      # 선택적 능력 추가

  exec/
    workspace.py      # 워크트리·outbox 생성과 정리, 통합 브랜치 머지
    runner.py         # 한 task 의 attempt 실행 (sequential)
    scheduler.py      # 병렬 스케줄링, path-conflict 직렬화, 머지 큐
    verify.py         # AC 실행, diff 판정, 경로 스코프, verdict 산출
    handoff.py        # outbox 아티팩트 정규화, required 게이트, TaskOutput 병합
    review.py         # 리뷰 wave, finding 병합, fixer
    tdd.py            # TDD 모드 — test-author / red gate / implementation 단계 (06)

  context/
    builder.py        # 계층 조립, provenance 표시, 프롬프트 구획 규약 문구
    repomap.py        # 저장소 구조 요약
    slicing.py        # 문서 앵커·심볼 단위 절취
    budget.py         # 토큰 예산 배분과 탈락

  eval/               # 커널 밖
    fixtures.py  arms.py  metrics.py  report.py
    swebench.py       # 외부 벤치마크 → fixture 컨버터

  resources/          # 패키지와 함께 배포되는 런타임 리소스
    schemas/          # jsonschema 정의 (claim, handoff, task, spec, event, findings)
    templates/        # spec / plan / task 템플릿

evals/fixtures/<case>/
```

## 배포

하네스는 `pip install` 가능한 패키지로 배포되며 **설치본만으로 동작한다.** 다른 저장소에
얹기 위해 이 저장소의 클론이 필요하다면 그것은 프레임워크가 아니라 스크립트 모음이다.

- 런타임에 읽는 리소스(jsonschema 정의, 저작 템플릿)는 전부 `harness/resources/` 안에 있다.
  **저장소 루트의 형제 디렉토리를 런타임에 읽지 않는다.**
- 대상 저장소는 `--repo` 로 지정한다. 하네스 코드가 그 저장소 안에 있을 필요는 없다.
- `evals/fixtures/` 와 `tests/` 는 이 저장소의 개발 자산이지 런타임 리소스가 아니다.
  배포물에 담지 않는다.

## 의존 방향

```
models · errors · schemas · paths
  ↑
events · store · dag · git · risk · probes · policy
  ↑
exec/* · context/* · spec · analyze · converge
  ↑
eval/*
  ↑
cli
```

규칙:

- **순환 의존을 금지한다.** CI에서 import 그래프를 검사한다.
- `models`·`errors`·`schemas`·`paths`는 어떤 harness 모듈도 import하지 않는다. 넷은 같은 최하위 계층이며 서로도 import하지 않는다.
- `adapters/*`는 `models`와 `errors`에만 의존한다. 벤더 SDK를 커널 어디에도 노출하지 않는다.
- **`cli`가 `eval`을 호출한다.** `eval`은 `cli`를 import하지 않는다. `eval`이 의존하는 하위 API는 `store`(journal 읽기), `exec/runner`(실행), `adapters/registry`(arm별 어댑터 선택), `spec`(fixture 골격), `models`뿐이다.
- `eval`을 import하는 모듈은 `cli` 하나뿐이다.
- `sys.exit`는 `cli.py`에만 존재한다. 다른 모듈은 예외를 올리거나 값을 반환한다. 그래야 라이브러리로 쓰이고 테스트된다.

## 커널과 옵션

**커널** — 이것만으로 파이프라인이 끝까지 동작해야 한다.

```
models  events  store  dag  git  probes  policy  errors  config  schemas  paths
exec/{workspace, runner, verify, handoff}
adapters/{base, registry, conformance, mock, generic_cli}
cli
```

**옵션** — 제거해도 커널이 동작한다.

```
exec/scheduler   (병렬)
exec/review      (독립 리뷰)
exec/tdd         (TDD 모드 — 없으면 development.mode: tdd 인 task 가 fail-closed 로 error 다)
spec · analyze · converge   (스펙 파이프라인 — 저작과 게이트)
risk             (티어 결정 — 없으면 declared_risk 를 그대로 쓴다)
context/*        (고급 선택 — 없으면 task 계약에 명시된 파일만 넣는다)
learn            (지식 축적)
adapters/{claude_cli, codex_cli}
eval/*
container 프로파일
```

**M8의 완료 기준은 옵션 레이어를 전부 제거한 상태에서 커널이 동작함을 테스트로 증명하는 것이다.** 이 테스트가 커널/옵션 경계가 실재함을 강제한다.

## 단순성 관찰 지표 (soft budget)

커널 약 1,500줄은 **관찰 지표이지 합격 조건이 아니다.** 초과는 실패가 아니라 모듈 경계를 다시 볼 신호다.

**줄 수를 맞추려고 가독성을 희생하는 것은 금지한다.** 한 줄에 로직을 욱여넣거나, 이름을 줄이거나, 주석을 지우는 방식으로 지표를 맞추지 않는다.

`policy.py`와 `exec/handoff.py`는 커널이면서 나중에 추가된 책임이므로, 실측할 때 이 둘의 비중을 별도로 기록한다. 커널이 커진 원인이 이 둘이라면 그것은 정보이지 결함이 아니다.

## 확장 지점

새 기능을 추가할 때 만들어도 되는 것은 다음 셋뿐이다.

1. **새 어댑터** — `adapters/`에 파일 하나. conformance 스위트 통과가 합격 조건이다. 04 참조.
2. **새 probe 종류** — `probes.py`의 `kind` 하나. precondition 문법에 값이 하나 늘어난다.
3. **새 이벤트 타입** — `events.py`에 추가하고 `store.py`의 fold에 반영. 기존 이벤트의 의미를 바꾸지 않는다.

그 밖의 기능은 **먼저 어느 문서의 계약을 바꾸는지 밝히고** 진행한다. 계약을 바꾸지 않는 기능은 커널에 들어갈 이유가 없다.
