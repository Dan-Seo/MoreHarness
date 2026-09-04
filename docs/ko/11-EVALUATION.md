# 11 · 평가

*이 문서는 [`docs/11-EVALUATION.md`](../11-EVALUATION.md)의 번역이다. 둘이 어긋나면 영문이 canonical이다.*

이 문서는 **지표 정의의 canonical 위치**다.

> 원칙 7 — 개선은 측정된다.

하네스가 실제로 결과를 개선하는지 증명하지 못하면 이 프레임워크는 복잡성만 추가한 것이다. 그 증명을 위한 장치가 eval이다.

---

## 두 종류의 eval을 섞지 않는다

|  | 회귀 eval | 능력 eval |
|---|---|---|
| 묻는 것 | 오케스트레이션이 규약대로 동작하는가 | 하네스가 실제로 결과를 개선하는가 |
| 어댑터 | `mock` | 실제 벤더 어댑터 |
| 결정론 | 완전 | 비결정론 → 반복 실행 + 분산 보고 |
| 비용 | 0 | 실비 |
| 위치 | CI 필수 | 수동 또는 주기 실행 |
| 도입 | **M1** | **M7** |

섞으면 둘 다 못 쓰게 된다. CI가 비결정론적이 되거나, 능력 측정이 mock의 시나리오를 재확인하는 일이 된다.

---

## Fixture

```
evals/fixtures/<case>/
  seed/                   # 초기 저장소 전체
  tasks/                  # 선택. 없으면 seed 의 스펙에서 하네스가 plan/tasks 를 만든다
  grader/
    hidden_ac.yaml        # 채점 기준
    expected.yaml         # 기대 산출물 형태
  meta.yaml               # 난이도, 도메인, 예상 소요
```

`seed/`는 초기 저장소 전체다. 그래서 `.harness/`도, `specs/<slug>/spec.yaml`(03의 파일 배치)도 그 안에 있다. `.harness/`는 fixture가 자기 어댑터와 Command Policy를 선언하는 자리다.

### `grader/expected.yaml` — 회귀 eval의 채점 기준

회귀 eval이 묻는 것은 "오케스트레이션이 규약대로 동작했는가"다. 따라서 채점 대상은 산출된 코드가 아니라 **journal이 만든 최종 state**다.

```yaml
tasks:
  T-001: {verdict: verified, state: done}
  T-002: {verdict: blocked,  state: human_required}
open_debts: []              # 남아 있어야 할 debt 의 커맨드 목록
human_required: [T-002]
```

- 적지 않은 키는 채점하지 않는다. fixture가 주장하는 것만 적는다.
- `verdict`가 `null`인 것도 주장이다 — 03의 "verdict 없이 state만 갖는" 경우다.

`hidden_ac.yaml`은 최종 트리를 채점하며 능력 eval이 쓴다. 회귀 eval은 `mock`으로 돌므로 트리를 채점하지 않는다.

### hidden grader의 절대 규칙

> **grader의 AC는 task `acceptance`에도, 컨텍스트 조립에도 절대 들어가지 않는다.**

들어가는 순간 agent가 채점 기준에 최적화하므로 측정이 무의미해진다. 이것은 편의상의 관례가 아니라 eval의 타당성 자체다. `eval/fixtures.py`는 fixture를 로드할 때 `grader/`를 컨텍스트 소스에서 물리적으로 제외한다.

---

## Arm

| arm | 내용 |
|---|---|
| `raw` | 하네스 없이 스펙 전문을 한 번에 투입 |
| `harness-lite` | 커널만 (02의 커널/옵션 경계) |
| `harness-full` | 전체 |
| `ablation:<feature>` | 특정 기능만 제거 |

`raw`도 **얇은 측정 전용 래퍼**로 실행한다. 래퍼는 판정하지 않는다 — journal 에 남기는 것은 `run_started`/`run_finished`, `agent_started`/`agent_finished`, 그리고 fixture 에 `tasks/` 가 있으면 그 AC 합집합의 사후 실행(`ac_post_executed` 와 그것이 낳는 `command_policy_decision`)뿐이다. 판정 이벤트는 없다. 그래야 모든 arm의 지표가 같은 journal 스키마에서 나온다.

### arm 의 조립

| arm | 조립 |
|---|---|
| `raw` | 측정 래퍼. 프롬프트는 `specs/*/spec.yaml` 전문을 이어붙인 것이고, workspace 는 materialize 된 저장소의 워킹트리 자체다. outbox 는 저장소 밖이다. AC 사후 실행도 Command Policy 를 통과한다 |
| `harness-lite` | 커널만 — sequential runner. 컨텍스트 조립 없음, 리뷰 없음, `max_parallel` 은 1 로 강제 |
| `harness-full` | `harness run` 과 같은 조립 — 컨텍스트 빌더 + 리뷰 스테이지, `max_parallel > 1` 이면 병렬 스케줄러 |
| `ablation:<feature>` | `harness-full` 에서 하나만 제거. `feature` ∈ {`context`, `review`, `parallel`} |

---

## 모든 arm은 동일한 hidden grader로 채점된다

```
raw   harness-lite   harness-full   ablation:<f>
  │         │              │              │
  └─────────┴──────┬───────┴──────────────┘
                   ▼
           동일 hidden grader
                   ▼
            external_success
```

하네스의 ship 게이트로 arm을 비교하면 `raw`는 애초에 ship 게이트를 갖지 않으므로 비교가 성립하지 않는다. **채점자는 하네스 밖에 있어야 한다.**

### `grader/hidden_ac.yaml` — 능력 eval 의 채점 기준

```yaml
acceptance:
  - cmd: ["pytest", "-q", "tests/hidden"]
  - cmd: ["python", "-c", "import app"]
    shell: false
```

- 항목의 모양은 task `acceptance` 와 같다 — argv 리스트가 기본이고 `shell` 은 선언해야 한다.
- argv 항목의 `{grader}` 는 그 fixture 의 `grader/` 절대 경로로 치환된다. 채점에만 쓰는 자료를
  트리에 심지 않고 참조하기 위한 것이다. 치환은 grader 실행에서만 일어나며 컨텍스트 조립에는
  `grader/` 가 여전히 없다.
- 실행 cwd 는 **채점 대상 트리**, 타임아웃은 config `ac_timeout_s` 다.
- 전부 green 이면 그 실행은 grader 성공이다. `hidden_ac_pass_rate` 는 green AC 수 / 전체 AC 수다.
- grader 커맨드도 Command Policy 를 통과한다 — materialize 된 저장소의 config 기준이다. 정책이 막으면 그 AC 는 red 이고 사유가 `eval.json` 에 남는다.
- run journal 은 `run_finished` 로 닫혔으므로 **grader 는 journal 에 쓰지 않는다.** grader 의 결과와 정책 판정은 `eval.json` 이 갖는다.
- `hidden_ac.yaml` 이 없거나 `acceptance` 가 비어 있으면 능력 eval 은 그 fixture 를 **실행하지 않고 오류로 거부한다.** 채점 기준 없는 측정은 공허하게 성공할 뿐이다.

### 채점 대상 트리

grader 가 실행되는 cwd 는 **run 이 끝난 뒤 사용자가 갖게 되는 트리**다.

| 실행 | 채점 대상 |
|---|---|
| `raw` arm | agent 가 작업한 워킹트리 그 자체 |
| 하네스 arm | integration 브랜치가 있으면 그 tip 의 분리 체크아웃 (converge 와 같은 방식), 없으면 저장소 워킹트리 |

기준은 프로파일 선언이 아니라 **integration 브랜치의 존재**다 — task 별 프로파일 오버라이드가 섞여 있어도 ship 이 머지할 그 트리를 채점한다.

### `escape_rate` / `false_block_rate` 의 분모

- 측정 단위는 **실행 1회** (fixture × arm × repeat) 이며 하네스 arm 에만 정의된다.
- "verified 로 판정" = run 의 모든 task 의 최종 verdict 가 `verified`.
- "rejected/blocked 로 판정" = 최종 verdict 중 `rejected` 또는 `blocked` 가 하나 이상.
- `escape_rate` = verified 실행 중 grader 실패 비율. `false_block_rate` = rejected/blocked 실행 중 grader 성공 비율.
- 분모가 0 이면 지표는 null 이고 리포트에 `n/a` 로 표시한다.

---

## 지표 — canonical

### 외부 결과 지표 — arm 비교의 핵심

하네스 바깥의 oracle 기준이다.

| 지표 | 정의 |
|---|---|
| `grader_success_rate` | hidden grader 기준으로 성공한 fixture 비율. **arm 비교는 이 값으로만 한다** |
| `hidden_ac_pass_rate` | hidden AC 단위의 통과율 |

### 하네스 보정 지표 — 하네스의 판단이 얼마나 정확했는가

| 지표 | 정의 |
|---|---|
| `escape_rate` | 하네스는 `verified`로 판정했으나 hidden grader 는 실패로 본 비율 |
| `false_block_rate` | 하네스가 `rejected`/`blocked` 했으나 최종 트리를 hidden grader 로 채점하면 성공인 비율 |

**이 둘이 설계를 정당화하거나 반증한다.**

- `escape_rate`가 높다면 AC와 리뷰에 검증력이 없다는 뜻이다. 06이 인정한 "남는 최대 리스크"의 수치화다.
- `false_block_rate`가 높다면 하네스가 올바른 작업을 막고 있다는 뜻이다. 차등 판정과 리뷰 티어를 다시 봐야 한다.

### 내부 지표

| 지표 | 정의 |
|---|---|
| `ship_gate_pass_rate` | ship 게이트를 통과한 비율. **하네스 arm 에만 의미가 있으며 arm 비교에 쓰지 않는다** |

### 비용·속도 지표

| 지표 | 원천 이벤트 |
|---|---|
| `first_pass_rate` | `verdict_assigned` (attempt 1 에서 verified 인 비율) |
| `retry_count` | `verdict_assigned` 의 attempt 최대값 |
| `review_waves` | `review_finding`, `fixer_dispatched` |
| `wall_time_s` | `run_started`, `run_finished` |
| `agent_time_s` | `agent_finished.duration_s` 합 |
| `tokens_in` / `tokens_out` | `agent_finished.usage` |
| `cost_usd` | `agent_finished.usage` — 벤더 미보고 시 `null`. **추정하지 않는다** |
| `human_interventions` | `human_required` 로 간 횟수 |
| `convergence` | ship 시점의 커버리지 %, 드리프트 수, 수렴까지의 wave 수 |
| `context_tokens` | `context.manifest.json` 의 `total_tokens` |

**전부 journal의 projection이며 별도 계측 코드가 없다.** 지표를 위해 코드에 카운터를 심지 않는다. 새 지표가 필요하면 먼저 필요한 이벤트가 있는지 본다.

`cost_usd`는 벤더가 보고하지 않으면 `null`이다. 토큰 단가 추정 모델을 만들지 않는다. `null`인 지표는 리포트에 `n/a`로 표시하고 평균에서 제외한다.

---

## 실행과 보고

```
harness eval run --fixtures evals/capability --arms raw,harness-full --repeat 3
```

- `--fixtures <dir>` 는 `<dir>/<case>/seed/` 를 찾고, 없으면 `<dir>/fixtures/<case>/seed/` 를 찾는다.
- 이 저장소는 회귀 fixture 를 `evals/fixtures/` 에, 능력 fixture 를 `evals/capability/` 에 둔다. 능력 eval 은 `hidden_ac.yaml` 이 비어 있는 fixture 를 거부하므로 둘을 한 디렉토리에 섞지 않는다.
- 산출은 `--out` (기본: `--fixtures` 디렉토리) 에 쓰는 `eval-report.md` 와 `eval.json` 이다.
- 실행 저장소들은 시스템 temp 의 작업 디렉토리에 남고, `eval.json` 이 그 경로를 기록한다. 실패를 파고들 때 journal 이 필요하기 때문이다.

### 능력 eval 보고 규칙

- **중앙값과 분산을 함께 보고한다.** 비율 지표(`grader_success_rate` 등)는 실행 단위 비율로 집계하고 fixture 별 내역을 나열한다. 연속 지표(`wall_time_s`, `agent_time_s`, 토큰, `cost_usd`, `context_tokens`)는 **중앙값과 [min, max]** 를 보고한다. null 값은 제외하고, 전부 null 이면 `n/a` 다.
- **단일 실행 수치를 개선의 근거로 제시하는 것을 금지한다.** LLM 실행은 비결정론적이므로 한 번의 좋은 결과는 정보가 아니다.
- `--repeat`의 기본값은 3이며, 그보다 적게 실행한 결과에는 리포트가 경고를 표시한다.
- 각 arm의 실패 사례를 fixture 단위로 나열한다. 집계만 보여주면 어디가 왜 실패했는지 알 수 없다.

---

## 아키텍처상의 위치

`eval`은 **커널 밖**이다. 02의 의존 방향을 따른다.

```
exec/* · context/*  ←  eval/*  ←  cli
```

- **`cli`가 `eval`을 호출한다.** `eval`은 `cli`를 import하지 않는다.
- `eval`이 의존하는 하위 API는 `store`(journal 읽기), `exec/runner`(실행), `adapters/registry`(arm별 어댑터 선택), `spec`(`tasks/` 없는 fixture 의 골격), `models`뿐이다.
- `eval`을 import하는 모듈은 `cli` 하나다. 순환 의존은 금지한다.
- eval fixture가 실행하는 커맨드도 Command Policy를 통과한다. 06 참조.

---

## 외부 벤치마크

외부 대형 벤치마크 연동은 **fixture 어댑터를 하나 더 만드는 문제**로 격리한다. 외부 벤치마크의 케이스 형식을 `evals/fixtures/<case>/` 구조로 변환하는 계층 하나면 나머지는 그대로 동작한다. 커널도 지표 정의도 바뀌지 않는다.

```
harness eval import --benchmark swebench --instances <jsonl> --repos <dir> --out <dir>
```

`--instances` 는 SWE-bench 인스턴스의 JSON Lines 파일이고, `--repos` 는 그 인스턴스들이 가리키는 저장소의 **로컬 체크아웃이 있는 디렉토리**다 (`<owner>__<name>` 또는 `<owner>/<name>`). **컨버터는 네트워크를 쓰지 않는다** — 받아오는 것은 사람이 미리 하고 컨버터는 변환만 한다.

### 필드 대응

| SWE-bench 필드 | fixture |
|---|---|
| `instance_id` | 케이스 디렉토리 이름 |
| `repo` + `base_commit` | `seed/` — 로컬 체크아웃에서 그 커밋의 트리만 꺼낸다. `.git` 은 따라오지 않는다 |
| `problem_statement` | `seed/specs/<instance_id>/spec.yaml` 의 R-001 |
| `test_patch` | `grader/test_patch.diff` — **seed 에 넣지 않는다** |
| `FAIL_TO_PASS` · `PASS_TO_PASS` | `grader/hidden_ac.yaml` 의 acceptance |
| `version` · `environment_setup_commit` · `created_at` | `meta.yaml` |

테스트가 hidden 인 것이 이 벤치마크의 본질이고 그것은 위 「hidden grader 의 절대 규칙」과 같은 규칙이다. 그래서 테스트 패치는 `grader/` 에 있고 채점 시점에만 적용된다.

```yaml
# grader/hidden_ac.yaml
acceptance:
  - cmd: ["git", "apply", "{grader}/test_patch.diff"]
  - cmd: ["python", "-m", "pytest", "-q", "<FAIL_TO_PASS ...>"]
  - cmd: ["python", "-m", "pytest", "-q", "<PASS_TO_PASS ...>"]
```

- 첫 항목이 red 면 채점 자체가 성립하지 않은 것이므로 red 가 맞다.
- FAIL_TO_PASS 와 PASS_TO_PASS 를 각각 한 커맨드로 묶는다. 그래야 `grader_success_rate` 가 그 벤치마크 자신의 성공 정의(F2P 전부 통과 ∧ P2P 무회귀)와 같아진다.
- 테스트 식별자는 pytest node id 형식이다. 자기 러너를 쓰는 저장소는 생성된 `hidden_ac.yaml` 을 사람이 고친다. **컨버터는 변환기이지 벤치마크 실행기가 아니다** — 파이썬 환경 준비는 컨버터의 일이 아니다.

### 만들어지는 task

컨버터는 요구사항 하나(R-001)만 만들고 **`tasks/` 는 쓰지 않는다.** 위 「Fixture」의 규칙대로 하네스가 스펙에서 골격을 만들고, 그 골격에는 **보이는 AC 가 없다.** 채점 기준을 숨기는 것이 이 벤치마크의 본질이므로 하네스가 가진 증거는 diff 와 리뷰뿐이다. 이것은 컨버터의 결함이 아니라 이 벤치마크에서 하네스가 실제로 놓인 조건이며, `escape_rate` 가 그것을 드러낸다.

`grader/expected.yaml` 은 만들지 않는다 — 외부 벤치마크 fixture 는 능력 eval 용이고 회귀 eval 은 `mock` 으로 돈다.

### 건너뛰기와 멱등성

- 로컬 체크아웃이 없거나 `base_commit` 이 그 체크아웃에 없으면 **그 instance 만 건너뛰고 사유를 보고한다.** 하나가 없다고 전체 변환이 실패하지 않는다.
- `FAIL_TO_PASS` 와 `PASS_TO_PASS` 가 둘 다 비어 있으면 건너뛴다. 채점 기준 없는 fixture 는 만들지 않는다.
- 같은 `--out` 에 다시 돌리면 결과가 같다. 이미 있는 케이스 디렉토리는 통째로 다시 쓴다.
- `seed/.harness/` 는 컨버터가 기본값으로 만든다. 어댑터와 Command Policy 는 fixture 가 선언하는 것이므로 사람이 고쳐서 쓴다.
