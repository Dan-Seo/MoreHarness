# 13 · 제품 요구사항

*이 문서는 [`docs/13-PRD.md`](../13-PRD.md)의 번역이다. 둘이 어긋나면 영문이 canonical이다.*

> **누가 이것을 fork하고, fork한 사람이 무엇을 해야 하며, 무엇이 fork할 값어치를 만드는가.**

13개의 설계 문서는 엔지니어링 계약서다. 파이프라인, 판정 알고리즘, 어댑터 프로토콜을
규정한다. 그러나 이 하네스가 **누구를 위한 것인지**, fork한 사람이 첫 verified 실행
전에 무엇을 해야 하는지, 구현만 된 것이 아니라 **쓸 만하다**고 하려면 무엇이 참이어야
하는지는 어느 문서도 말하지 않는다. 이 문서가 그것이다.

이것은 요구사항이지 계약이 아니다. 어떤 코드 경로도 구속하지 않고 verdict를 추가하지 않는다.

## 이 문서가 canonical인 것

세 가지, 그리고 그 외에는 없다.

1. **대상 사용자** — 누가 이 저장소를 fork하고, 누가 fork하지 말아야 하는가.
2. **채택 경로** — fork한 사람이 자기 프로젝트에서 첫 verified 실행에 이르기까지 하는 일.
3. **제품 수용 기준** — 이 제품이 쓸 만하려면 무엇이 참이어야 하는가.

## 이 문서가 소유하지 않는 것

아무것도 재서술하지 않는다. 아래 주제는 모두 이미 canonical한 집이 있고, 이 문서는
그곳을 링크할 뿐이다.

| 주제 | canonical 문서 |
|---|---|
| 이 프레임워크가 존재하는 이유 | [`00-OVERVIEW.md` § Why this is needed](00-OVERVIEW.md) |
| 기술적 비목표 | [`00-OVERVIEW.md` § Explicit Non-Goals](00-OVERVIEW.md) |
| 런타임·의존성 제약 | [`00-OVERVIEW.md` § Constraints](00-OVERVIEW.md) |
| 파이프라인 단계와 CLI | [`00-OVERVIEW.md`](00-OVERVIEW.md) |
| 여기서 쓰는 용어 | [`01-CONCEPTS.md`](01-CONCEPTS.md) |
| 격리가 보장하는 것과 하지 않는 것 | [`05-EXECUTION-ISOLATION.md`](05-EXECUTION-ISOLATION.md), [`09-SECURITY.md`](09-SECURITY.md) |
| 지표 정의 | [`11-EVALUATION.md`](11-EVALUATION.md) |
| 마일스톤 범위 | [`12-ROADMAP.md`](12-ROADMAP.md) |

**여기 있는 요구사항이 저 문서들과 모순되면, 이기는 쪽은 설계 문서이고 틀린 쪽은 이
문서다.** 제품 요구사항은 계약을 다시 정의할 권한이 없다.

## 대상 사용자

### fork 소유자 — 주 사용자

자기 코드베이스를 가지고 있고, 코딩 에이전트를 그 안에서 일하게 하고 싶지만, 에이전트가
무엇을 했다는 **그 말 자체를 믿을 생각은 없는** 사람.

이 사람에 대해 전제하는 것:

- 이미 Git 저장소와 Python 3.11+, 인증된 벤더 CLI를 가지고 있다.
- 하네스를 **자기 프로젝트 안에서** 돌린다. 이 저장소 안에서가 아니다.
- 수용 기준을 손으로 쓴다. `spec`·`plan`·`tasks`는 뼈대만 만들고, 그 뼈대가 비어 있는
  동안 `analyze`가 막는다.

마지막 항목은 관찰이 아니라 요구사항이다. 생성된 수용 기준을 사용자가 읽지 않고 받아들일
것이라고 제품이 전제해서는 안 된다. 에이전트가 자기 합격 조건을 직접 쓰는 것이야말로 이
프레임워크가 막으려고 존재하는 실패이기 때문이다.

### 하네스 튜너 — 보조 사용자

설정을 비교하는 사람. 벤더를 바꾸거나, 프로파일을 바꾸거나, 컨텍스트 예산을 바꿔 보고
인상이 아니라 숫자를 필요로 한다. `harness eval`을 쓰고 [`11-EVALUATION.md`](11-EVALUATION.md)에
정의된 지표를 읽는다.

### 대상이 아닌 사용자

- 호스팅 서비스나 데몬, 웹 UI를 원하는 사람. 커널에는 그런 것이 없다.
- 에이전트가 자기 작업을 스스로 머지하기를 원하는 사람. `ship`은 사람의 명령이다.
- 사람 입력이 필요 없는 한 방짜리 래퍼를 원하는 사람. 수용 기준은 사람이 쓰고, 그
  단계는 제거 대상이 아니다.

## 채택 경로

두 개의 저장소가 동시에 관여한다. 한 번 설치하는 이 저장소, 그리고 모든 실행이 일어나는
대상 프로젝트다. 이 둘을 혼동하는 것이 가장 흔한 첫 실수이므로, 아래 각 단계는 자기
확인 방법을 함께 가진다.

| # | 단계 | 무엇이 바뀌나 | 확인 |
|---|---|---|---|
| 1 | 클론에서 설치 | 대상 프로젝트에는 아무 변화 없음 | `harness --help`가 실행된다 |
| 2 | 대상 저장소에서 `harness init` | `.harness/` 생성 | `harness doctor`가 지적 없이 통과한다 |
| 3 | `.harness/constitution.md`에 프로젝트 규칙을 쓴다 | control plane | 규칙이 잘리지 않고 모든 task 프롬프트에 들어간다 |
| 4 | `mock` 어댑터 슬롯을 실제 벤더로 교체 | `.harness/config.yaml` | `harness doctor`가 어댑터 preflight를 돌린다 |
| 5 | `command_policy`를 읽고 프로젝트에 맞게 고친다 | `.harness/config.yaml` | 기본값이 fail-closed이므로, 목록에 없는 것은 무인 실행되지 않는다 |
| 6 | 한 사이클을 돌리며 수용 기준을 손으로 채운다 | `spec`·`plan`·`tasks` 산출물 | `harness status`가 verified task를 보고한다 |

**이 경로의 어떤 단계도 하네스 소스를 고치도록 요구해서는 안 된다.** fork한 사람은
패치가 아니라 control plane을 통해 하네스를 자기 프로젝트에 맞춘다. 이것이 이 저장소를
프로젝트마다 다시 쓰는 코드베이스가 아니라 **fork 템플릿**으로 만드는 조건이다.

## 커스터마이징 표면

프로젝트마다 정당하게 달라지는 것은 전부 아래 중 한 곳에서 설정된다.

| 프로젝트마다 다른 것 | 설정하는 곳 | canonical 문서 |
|---|---|---|
| 어떤 에이전트 CLI가 도는가 | `.harness/config.yaml`의 `adapters`와 `defaults.adapter` | [04](04-AGENT-ADAPTER.md) |
| 내장 어댑터가 없는 벤더 | 같은 파일의 `generic_cli` 어댑터 타입 | [04](04-AGENT-ADAPTER.md) |
| 에이전트가 절대 어기면 안 되는 규칙 | `.harness/constitution.md` | [01](01-CONCEPTS.md), [07](07-CONTEXT-SELECTION.md) |
| 무인 실행을 허용할 커맨드 | `.harness/config.yaml`의 `command_policy` | [06](06-VERIFICATION-REVIEW.md) |
| 실행을 얼마나 격리할 것인가 | `defaults.profile` | [05](05-EXECUTION-ISOLATION.md) |
| 얼마나 병렬로 돌릴 것인가 | `defaults.max_parallel` | [05](05-EXECUTION-ISOLATION.md) |
| 무엇을 완료로 볼 것인가 | `tasks/*.task.yaml`의 수용 기준 | [03](03-DATA-MODEL.md), [06](06-VERIFICATION-REVIEW.md) |

이 표에 소스 수정은 하나도 없다. 하네스가 이름도 들어 본 적 없는 벤더도 `generic_cli`와
설정만으로 닿는다. 어떤 프로젝트를 `harness/`를 패치하지 않고는 지원할 수 없다면, 그것은
fork 소유자가 할 일이 아니라 **커스터마이징 표면의 결함**이다.

## 제품 수용 기준

제품 수준의 기준이며, 각각은 이미 관찰 가능하다. 새로운 검사를 도입하지 않는다. 제품이
주장하는 바를 실제로 한다는 **증거의 위치**를 가리킬 뿐이다.

| # | 기준 | 관찰되는 곳 |
|---|---|---|
| 1 | fork한 사람이 하네스 소스를 고치지 않고 자기 프로젝트에서 동작하는 control plane에 도달한다 | `test_doctor_passes_on_a_freshly_initialized_repo`, `test_init_creates_the_control_plane` |
| 2 | 벤더 CLI 교체가 설정 변경이다 | `test_generic_cli_is_registered`, `test_the_adapter_passes_the_conformance_suite` |
| 3 | 에이전트의 자기 보고가 결과를 결정하지 않는다 | `test_agent_result_has_no_success_field`, [06](06-VERIFICATION-REVIEW.md) |
| 4 | 사람의 명령과 신선한 게이트 없이는 아무것도 사용자 브랜치에 닿지 않는다 | `test_ship_requires_a_fresh_converge`, `test_an_open_debt_blocks_ship_until_waived` |
| 5 | 중단된 run이 state 파일 손질이 아니라 journal로 재개된다 | `test_doctor_rebuilds_a_stale_state_snapshot`, `test_run_resume_picks_up_the_named_run` |
| 6 | 옵션 층 전체를 걷어내도 커널이 그대로 쓸 만하다 | `test_the_pipeline_finishes_without_the_option_layer` |
| 7 | 설정 변경이 개선이라고 주장하는 대신 보여 줄 수 있다 | `harness eval`, [11](11-EVALUATION.md) |

관찰 가능하지 않게 된 기준은 잃어버린 기준이다. 주장은 남겨 둔 채 증거만 없애는 것이 이
표가 잡으려는 실패다.

## 제품 범위 밖

아래는 제품 수준의 결정이며, [`00-OVERVIEW.md`](00-OVERVIEW.md)의 기술적 비목표와는 구분된다.

- **fork한 쪽의 설정은 upstream으로 돌아오지 않는다.** `.harness/` 디렉토리, 벤더 선택,
  에디터 플러그인 설정은 그것을 만든 프로젝트의 것이다. 이 저장소는 벤더 중립을 유지해서
  다음 fork가 중립 지점에서 출발하게 한다.
- **프로젝트별 프리셋을 여기서 배포하지 않는다.** 한 종류의 프로젝트만 원하는 프리셋은
  그 프로젝트의 fork에 속한다.
- **수용 기준 작성을 자동화하지 않는다.** spec에서 그것을 생성하는 순간
  [`00-OVERVIEW.md`](00-OVERVIEW.md)의 "Why this is needed"가 묘사한 자기 채점 루프가
  그대로 되살아난다.
