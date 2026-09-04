# 00 · 개요

*이 문서는 [`docs/00-OVERVIEW.md`](../00-OVERVIEW.md)의 번역이다. 둘이 어긋나면 영문이 canonical이다.*

> **Agents propose. Harness verifies. Evidence decides.**

## 이 프레임워크가 하는 일

코딩 agent에게 일을 시키고, **그 결과가 진짜인지 하네스가 직접 확인한다.**

agent는 코드를 쓰고 자기가 무엇을 했는지 보고한다. 하네스는 그 보고를 판정 근거로 쓰지 않는다. 대신 acceptance criteria를 직접 실행하고, git diff를 직접 읽고, 변경 경로를 직접 판정한다. 판정의 근거는 하네스가 만든 관측치뿐이다.

## 왜 이것이 필요한가

agent 오케스트레이터가 흔히 밟는 경로가 있다.

> 제어 루프가, agent가 직접 쓴 상태 파일에서 진실을 읽는다.

agent를 호출하고, agent가 `status: completed`라고 써 넣은 JSON을 다시 읽어 그것을 판정으로 삼는다. 한 번 이렇게 하면 나머지가 전부 따라온다. 검증을 실행할 이유가 없어지고, diff를 볼 이유가 없어지고, exit code가 0이 아니어도 경고만 남기게 된다. 리뷰는 사람이 기억할 때만 돌고, 재시도는 같은 실패를 반복하며, 무엇이 나아졌는지 측정할 방법이 없다.

v2는 그 한 지점을 뒤집는 데서 출발한다. **판정 권한은 agent에게 없다.**

| 흔한 양상 | v2의 대응 | 문서 |
|---|---|---|
| agent가 완료를 자가 선언 | 하네스가 독립 증거로 판정 | 06 |
| 매 호출마다 문서 전량 주입 | 계층형 컨텍스트 + 예산 | 07 |
| 특정 벤더 CLI가 실행기에 하드코딩 | 어댑터 프로토콜 + conformance | 04 |
| 권한 우회 플래그 상시, 메인 워크트리에서 실행 | 실행 프로파일 + 저장소 밖 워크트리 | 05 |
| agent·planner가 쓴 커맨드를 무검증 실행 | Command Policy | 06, 09 |
| 선형 실행만 가능 | Task DAG + 병렬 스케줄러 | 03, 05 |
| 독립 리뷰가 실행 경로에 없음 | effective_risk 티어 + bounded wave | 06 |
| 단계 간 인계가 산문 한 줄 | 구조화 handoff 계약 | 03 |
| 스펙↔구현 일치를 확인하지 않음 | R-### 추적 + `converge` | 08 |
| 한 단계의 blocked가 런 전체를 중단 | task-local blocked | 03 |
| 재개가 "상태 JSON 손으로 고치기" | journal 재생 | 10 |
| 개선을 측정할 방법이 없음 | eval 프레임워크 | 11 |

## 파이프라인

```
Intent → Spec → Plan → Task DAG → Analyze (사전 게이트)
      → Context Selection → Isolated Execution
      → Deterministic Verification → Independent Review
      → Converge (사후 게이트) → Ship → Learn
                                  ↘ Evaluate (journal에서 계측)
```

각 화살표는 하네스가 소유한다. agent는 `Isolated Execution` 안에서만 동작하고, 자기 결과를 다음 단계로 통과시킬 권한이 없다.

## 설계 원칙과 강제 지점

원칙은 선언이 아니라 코드의 특정 지점에서 강제된다. 강제 지점이 없는 원칙은 원칙이 아니다.

| # | 원칙 | 강제 지점 | 문서 |
|---|---|---|---|
| 1 | 하네스가 진실을 판정한다 | `exec/verify.py`. agent 산출물은 outbox에 격리되고 하네스가 읽어 검증한 뒤에만 승격 | 03, 04, 06 |
| 2 | 긴 대화보다 fresh context | task 단위 프로세스 분리. 상태는 대화가 아니라 아티팩트로 이어진다 | 03, 04 |
| 3 | 컨텍스트는 덤프가 아니라 선택 | `context/builder.py` + 토큰 예산 + `context.manifest.json` | 07 |
| 4 | 리뷰는 독립적이고 유한하다 | effective_risk 티어 + `MAX_REVIEW_WAVES` + 예산 상한 | 06 |
| 5 | 격리는 하네스가 강제한다 | `exec/workspace.py` + `policy.py` + 사후 경로 판정 | 05, 06, 09 |
| 6 | 단순한 커널, 선택적 고급 기능 | 커널/옵션 분리. 옵션을 전부 제거해도 커널이 동작함을 테스트로 증명 | 02, 12 |
| 7 | 개선은 측정된다 | `harness eval`. 모든 지표는 journal의 projection이며 별도 계측 코드가 없다 | 11 |

## CLI 지도

```
harness init                  저장소에 .harness/ 생성
harness spec <intent>         Intent → Spec (R-### 부여)
harness clarify               [NEEDS CLARIFICATION] 해소
harness plan                  Spec → Plan
harness tasks                 Plan → Task DAG
harness analyze               구현 전 게이트. 실패 시 run 을 막는다
harness run [--resume <id>]   DAG 실행
harness converge              구현 후 게이트. 커버리지·드리프트·debt
harness ship                  통합·배포 게이트
harness learn                 knowledge card 승격/폐기
harness eval run              회귀·능력 평가
harness eval import           외부 벤치마크 → fixture 변환
harness status                현재 run 상태, open_debts, human_required
harness doctor                일관성 검사 및 복구
```

## 문서 지도

한 개념의 **canonical 정의는 정확히 한 문서에만** 있다. 다른 문서는 그곳을 참조하며 재서술하지 않는다.

| 문서 | 역할 | 이 문서가 canonical인 것 |
|---|---|---|
| `00-OVERVIEW.md` | 목표·파이프라인·CLI·원칙 강제 지점 | 원칙 추적표 |
| `01-CONCEPTS.md` | 용어 | 용어 정의 |
| `02-ARCHITECTURE.md` | 모듈 경계·의존 방향·커널/옵션 | 의존 방향 |
| `03-DATA-MODEL.md` | 스키마·상태 기계·이벤트 로그·파일 배치 | **verdict·state 정의**, 이벤트 목록 |
| `04-AGENT-ADAPTER.md` | 어댑터 프로토콜·`generic_cli`·conformance | 어댑터 계약 |
| `05-EXECUTION-ISOLATION.md` | 실행 프로파일·워크스페이스·병렬 | **프로파일 보장/미보장** |
| `06-VERIFICATION-REVIEW.md` | AC 생명주기·판정·리뷰 | **판정 알고리즘, Command Policy** |
| `07-CONTEXT-SELECTION.md` | 컨텍스트 조립·예산 | **context provenance/신뢰** |
| `08-CONVERGENCE-LEARN.md` | `analyze`/`converge`/ship/지식 | ship 게이트, debt 원장 |
| `09-SECURITY.md` | 신뢰 경계·위협 모델·비보장 | 위협 모델 |
| `10-FAILURE-RECOVERY.md` | 실패 처리·크래시·재개 | **실패 분류표** |
| `11-EVALUATION.md` | 회귀/능력 평가 | **지표 정의** |
| `12-ROADMAP.md` | M0~M8 | 마일스톤 범위 |

## 제약

- 언어: Python. 의존성은 **stdlib + `PyYAML` + `jsonschema`** 뿐이다.
- 저장소에 DB·서버·데몬을 두지 않는다. 상태는 파일이다.
- 커널에 웹 UI가 없다.

## 명시적으로 만들지 않는 것

커스텀 DSL, 분산 실행, 벤더가 보고하지 않는 비용의 추정 모델, 이전 버전 호환 계층, 거대한 policy engine(설정 기반 규칙 목록과 fail-closed 기본값이 전부다), `verified_with_warning` 같은 파생 verdict.

`container` 프로파일, tree-sitter 기반 repo map, cross-model 리뷰, 외부 벤치마크 연동은 **옵션**이며 커널에 속하지 않는다.
