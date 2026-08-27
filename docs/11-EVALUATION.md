# 11 · 평가

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
  seed/                   # 초기 저장소 상태
  spec.yaml
  tasks/                  # 선택. 없으면 하네스가 plan/tasks 를 만든다
  grader/
    hidden_ac.yaml        # 채점 기준
    expected.yaml         # 기대 산출물 형태
  meta.yaml               # 난이도, 도메인, 예상 소요
```

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

`raw`도 **얇은 측정 전용 래퍼**로 실행한다. 래퍼는 판정하지 않고 `agent_started`, `agent_finished`, `ac_post_executed` 이벤트만 남긴다. 그래야 모든 arm의 지표가 같은 journal 스키마에서 나온다.

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
harness eval run --fixtures evals/ --arms raw,harness-full --repeat 3
```

산출은 `eval-report.md`와 `eval.json`.

### 능력 eval 보고 규칙

- **중앙값과 분산을 함께 보고한다.**
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
- `eval`이 의존하는 하위 API는 `store`(journal 읽기), `exec/runner`(실행), `adapters/registry`(arm별 어댑터 선택), `models`뿐이다.
- `eval`을 import하는 모듈은 `cli` 하나다. 순환 의존은 금지한다.
- eval fixture가 실행하는 커맨드도 Command Policy를 통과한다. 06 참조.

---

## 외부 벤치마크

SWE-bench 같은 외부 대형 벤치마크 연동은 **fixture 어댑터를 하나 더 만드는 문제**로 격리한다. 외부 벤치마크의 케이스 형식을 `evals/fixtures/<case>/` 구조로 변환하는 계층 하나면 나머지는 그대로 동작한다.

커널도 지표 정의도 바뀌지 않으므로 Post-MVP(M8)로 둔다.
