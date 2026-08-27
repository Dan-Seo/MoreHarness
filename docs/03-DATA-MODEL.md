# 03 · 데이터 모델

이 문서는 **verdict와 state의 canonical 정의**, 그리고 **이벤트 목록의 canonical 정의**를 갖는다.

---

## Spec

```yaml
# specs/<slug>/spec.yaml
slug: user-api
intent: "사용자 CRUD API 를 만든다"
requirements:
  - id: R-001
    statement: "POST /api/users 로 사용자를 생성할 수 있다"
    rationale: "가입 플로우의 전제"
    acceptance_hint: "생성 후 201 과 id 를 반환"
  - id: R-002
    statement: "이메일 중복은 409 로 거절한다"
open_questions:
  - "[NEEDS CLARIFICATION] 소프트 삭제인가 하드 삭제인가"
```

`R-###`는 스펙 안에서 유일하고 재사용되지 않는다. 요구사항이 삭제되면 번호는 결번으로 남는다. 번호를 재사용하면 과거 run의 커버리지 기록이 거짓이 된다.

`open_questions`에 `[NEEDS CLARIFICATION]`이 하나라도 남아 있으면 `analyze`가 `run`을 막는다.

---

## Task 계약

```yaml
# tasks/T-003.task.yaml
id: T-003
name: api-layer
kind: implementation          # implementation | analysis | readonly
satisfies: [R-002, R-005]
depends_on: [T-001]
risk: medium                  # trivial | low | medium | high | critical (선언값)
agent: default
profile: worktree             # safe | worktree | container | unsafe

allowed_paths:   ["src/api/**", "tests/api/**"]
forbidden_paths: []           # config 의 전역 금지 목록과 합집합으로 적용된다

preconditions:
  - {kind: env_var, name: DATABASE_URL}       # 존재 여부만 검사. 값은 기록하지 않는다.
  - {kind: command, cmd: ["docker", "info"]}  # Command Policy 적용 대상
  - {kind: file, path: ".env.local"}

context:
  docs:    ["docs/ARCHITECTURE.md#api-layer"]
  files:   ["src/types/user.ts"]
  symbols: ["UserRepository"]

acceptance:                   # 하네스가 직접 실행한다. Command Policy 적용 대상.
  - cmd: ["npm", "run", "build"]
  - cmd: ["npm", "test", "--", "tests/api"]
    expect_fail_before: true

outputs:
  required: [public_api]      # 다음 task 실행에 반드시 필요한 agent 산출 필드
  optional: [decisions]

spec_hash: "sha256:..."       # 참조한 spec 의 해시. 드리프트 검출용.
```

### `cmd`는 argv 리스트다

문자열이 아니라 리스트다. 기본 실행이 `shell=False`이므로 셸 메타문자, 명령 치환, 파이프, 리다이렉션이 해석되지 않는다. 셸이 반드시 필요하면 `shell: true`를 명시해야 하고, 그 선언 자체가 Command Policy에서 자동으로 `require_approval`로 승격된다. 06 참조.

### `kind`가 결정하는 것

| `kind` | diff 기대 | 용도 |
|---|---|---|
| `implementation` | 비어 있지 않아야 한다 | 코드 변경 |
| `readonly` | 비어 있어야 한다 | 조사·확인 |
| `analysis` | 무관 | 산출물이 handoff 뿐인 작업 |

---

## Task Output — 출처와 필요도

`outputs`를 이해하는 축은 **누가 만드는가(provenance)** 와 **없으면 곤란한가(필요도)** 둘이다.

| 필드 | 산출 주체 | 성질 |
|---|---|---|
| `changed_files`, `created_files`, `diff_stat` | **하네스** (git diff 에서 계산) | 항상 존재한다. agent 협조가 필요 없다. **`required`에 적을 수 없다.** |
| `public_api`, `decisions`, 그 밖의 의미 요약 | **agent** (handoff artifact) | `required` 또는 `optional` |

- `outputs.required`가 비어 있으면 — 대부분의 task가 그렇다 — handoff의 유무는 판정에 아무 영향이 없다.
- 하네스가 계산하는 값은 누락될 수 없으므로 `required` 대상이 아니다. `required`에 하네스 산출 필드를 적는 실수는 `analyze`가 사전에 잡는다.
- 최종 `TaskOutput` 레코드는 **하네스 산출 필드 + 검증을 통과한 handoff 필드**의 병합이며, 병합과 기록은 하네스가 한다.
- 병합 결과 안에서도 **하네스 산출 필드(fact)와 agent 산출 필드(untrusted)의 출처를 구분해 표기한다.** 07 참조.

---

## Claim과 Handoff는 별개 파일이다

둘은 outbox의 서로 다른 파일이다. 하나가 깨져도 다른 하나는 살아남는다. 서사와 데이터는 소비자도 수명도 다르기 때문이다.

### Claim envelope — optional, 힌트

```json
{
  "schema": "harness.claim/v1",
  "task_id": "T-003",
  "outcome_claim": "implemented",
  "commands_run": [{"cmd": "npm test", "exit_code": 0}],
  "blocked_hint": "DATABASE_URL 이 없어 보임"
}
```

`outcome_claim`은 `implemented | blocked | infeasible`.

필드 이름이 `blocked_reason`이 아니라 **`blocked_hint`** 인 것은 의도적이다. 이것은 결론이 아니라 하네스가 확인해 볼 가설이며, 하네스가 probe로 뒷받침하지 못하면 인정되지 않는다. 06 참조.

### Handoff artifact — 계약

```json
{
  "schema": "harness.handoff/v1",
  "task_id": "T-003",
  "public_api": ["POST /api/users", "GET /api/users/:id"],
  "decisions": ["인증은 미들웨어에서 처리하고 라우트 핸들러는 인증을 가정한다"]
}
```

`schema` 필드가 없거나 jsonschema 검증에 실패하면 `*.invalid.json`으로 보존되고 이후 판정에서 `None`으로 취급된다. **보고가 깨진 것이 구현이 깨진 것을 의미하지는 않는다.**

---

## Verdict — canonical

verdict는 **정확히 다섯 개**다.

| verdict | 의미 |
|---|---|
| `verified` | 하네스 소유 증거가 전부 통과했고 required handoff 게이트도 통과했다 |
| `rejected` | 증거가 목표 미달을 보인다 (회귀, red→green 미달, blocking finding 잔존) |
| `blocked` | 시스템은 정상이나 외부 준비물이 없어 진행할 수 없다 |
| `error` | 하네스·어댑터 결함이 의심되거나 실행이 유효하게 성립하지 않았다 |
| `budget_exhausted` | 예산 소진 |

**`repairing`, `needs_replan`, `integration_conflict`, `human_required`는 verdict가 아니라 state다.**

---

## State — canonical

state는 워크플로 위치를 나타내는 **별개의 축**이다.

| state | 의미 |
|---|---|
| `pending` | 의존이 아직 충족되지 않았다 |
| `ready` | 디스패치 대기 |
| `precheck` | `preconditions` + 어댑터 `preflight` 실행 중. agent 는 아직 실행되지 않았다 |
| `running` | agent 프로세스 실행 중 |
| `executed` | agent 프로세스 종료. **exit code 와 claim 유무에 무관하다** |
| `verifying` | 정규화 + AC post + diff·경로 판정 중 |
| `reviewing` | 리뷰 wave 진행 중 |
| `repairing` | 구현 증거는 전부 통과했으나 required handoff 가 없거나 invalid 하다. **handoff artifact 만** 재생성한다 |
| `needs_replan` | task 정의를 고쳐야 한다 |
| `integration_conflict` | 머지 충돌 |
| `human_required` | 사람 개입 없이는 진행 불가 |
| `done` | 종료. verdict `verified` 로만 도달한다 |

`claimed` 상태는 존재하지 않는다. claim은 optional이므로 상태 기계가 claim을 기다릴 수 없다.

### 상태 전이

```
pending ─(의존 verified)─> ready ─> precheck ─> running ─> executed ─> verifying
                                        │                                  │
                            precheck 실패│                     리뷰 필요 시  ├─> reviewing ─┐
                                        │                                  │              │
                                        v                                  v              v
                              blocked | error                        (handoff gate) <─────┘
                                                                           │
                                          required handoff 누락/invalid ────┤
                                                                    │      │
                                                                    v      v
                                                              repairing   verdict = verified ─> done
```

`precheck` 실패 시 agent를 실행하지 않고 verdict `blocked` 또는 `error`가 부여된다. 분류 기준은 10.

`repairing`이 성공하면 verdict `verified` → `done`. 시도가 소진되면 `human_required`(reason: `handoff_missing`).

`blocked`는 해당 task와 그 하위 의존만 막는다. **런 전체를 중단하지 않는다.** 막히지 않은 가지는 계속 진행하고, run은 정상 종료하면서 무엇이 왜 막혔는지 요약한다. 10 참조.

### verdict → next_state

| verdict | 조건 | next_state |
|---|---|---|
| `verified` | — | `done` |
| `rejected` | 시도 남음 | `ready` |
| `rejected` | 시도 소진 | `needs_replan` |
| `blocked` | — | `human_required` |
| `error` | transient·재시도 여지 있음 | `ready` |
| `error` | system defect·반복 | `human_required` |
| `budget_exhausted` | — | `human_required` |

verdict 없이 state만 갖는 경우가 셋 있다.

| 상황 | verdict | next_state |
|---|---|---|
| required handoff 누락/invalid (구현 증거는 통과) | — | `repairing` |
| 머지 충돌 | — | `integration_conflict` |
| task 정의 결함 | — | `needs_replan` |

**task 정의 결함은 재시도가 의미 없으므로 시도 잔량과 무관하게 `needs_replan`으로 간다.** 여기 속하는 것은 셋이다.

| reason | 상황 |
|---|---|
| `policy_denied_at_runtime` | `deny` 커맨드가 런타임에 도달했다 (`analyze`가 놓친 경우) |
| `ac_not_discriminating` | baseline 에서 `expect_fail_before: true` 인 AC 가 이미 통과했다 |
| `no_op_detected` | `implementation` task 인데 diff 가 비었고 AC 가 전부 통과했다 |

### verdict 부여 규칙

- `verdict_assigned`는 **attempt당 최대 한 번** 발생하며 payload에 `attempt` 번호를 기록한다.
- `rejected`/`error` → `ready` → 재시도 경로가 존재하므로, task 전체에서 verdict가 한 번뿐이라는 규칙은 성립하지 않는다.
- **task의 최종 verdict는 가장 큰 `attempt`의 `verdict_assigned`** 로 정의한다.
- `verified`는 terminal에서만 기록한다. 증거 조건을 통과했다는 이유만으로 즉시 기록하지 않는다. 순서는 06.

---

## Journal이 canonical, state는 projection

```
journal.jsonl   append-only · 불변 · 단일 writer     <- canonical
state.json      fold(journal) 의 스냅샷              <- 파생 캐시
```

- `seq`는 1부터 단조 증가하며 **결번이 없다.**
- 이벤트 `id = <run_id>-<seq:06d>`.
- `state.json`은 `last_applied_seq`를 갖는다. 언제든 journal을 처음부터 fold해 재구성할 수 있다.
- **쓰기 순서는 journal append + fsync → state 갱신이다.** 중간에 죽으면 스냅샷이 뒤처질 뿐 손실은 없다. 10 참조.
- 이벤트는 불변이다. 정정은 삭제나 수정이 아니라 **새 이벤트**로 한다.
- **병렬 실행에서도 journal writer는 오케스트레이터 프로세스 하나뿐이다.** 병렬화 대상은 agent 서브프로세스이지 상태 기록이 아니다.

`state == fold(journal)`은 `harness doctor`가 검사하는 불변식이다.

### 이벤트 레코드 공통 형태

```json
{"id": "run-20260827-1432-000042",
 "seq": 42,
 "ts": "2026-08-27T14:35:02.113+09:00",
 "type": "ac_post_executed",
 "run_id": "run-20260827-1432",
 "task_id": "T-003",
 "attempt": 1,
 "payload": {}}
```

`task_id`와 `attempt`는 run 수준 이벤트에서 `null`이다.

### 이벤트 목록 — canonical

| type | 언제 | payload 핵심 |
|---|---|---|
| `run_started` | run 시작 | `manifest`, `profile`, `adapter`, `max_parallel` |
| `precondition_checked` | precheck | `kind`, `name`, `ok`, `detail` (**값은 기록하지 않는다**) |
| `command_policy_decision` | 커맨드 실행 직전 | `cmd`, `verdict`, `rule`, `approver` |
| `task_dispatched` | 디스패치 | `context_manifest_ref`, `prompt_ref`, `effective_risk` |
| `agent_started` | 프로세스 시작 | `adapter`, `workspace`, `outbox` |
| `agent_finished` | 프로세스 종료 | `exit_code`, `duration_s`, `usage`(미보고 시 `null`), `runtime_failure` |
| `agent_exit_nonzero` | exit code ≠ 0 | `exit_code`, `stderr_tail` |
| `claim_received` | claim 정규화 통과 | `outcome_claim` |
| `claim_rejected` | claim schema 위반 | `error`, `path` |
| `claim_uncorroborated` | claim 힌트를 probe 가 뒷받침하지 못함 | `hint`, `probe`, `probe_result` |
| `handoff_received` | handoff 정규화 통과 | `fields` |
| `handoff_rejected` | handoff schema 위반 | `error`, `path` |
| `handoff_missing` | required handoff 부재 | `required`, `present` |
| `ac_baseline_executed` | agent 실행 전 | `cmd`, `exit_code`, `classification`, `expect_fail_before` |
| `ac_post_executed` | agent 실행 후 | `cmd`, `exit_code`, `classification`, `differential` |
| `debt_opened` | 사전 실패 AC 발견 | `debt_id`, `cmd`, `origin_task` |
| `debt_closed` | 해당 AC 가 green 이 됨 | `debt_id`, `closed_by` |
| `path_violation` | diff 가 허용 범위를 벗어남 | `paths`, `rule` |
| `risk_escalated` | effective_risk 상향 | `declared`, `path_floor`, `diff_floor`, `effective` |
| `review_finding` | 리뷰어 지적 | `wave`, `reviewer`, `severity`, `rule`, `file`, `line`, `blocking` |
| `fixer_dispatched` | fixer 호출 | `wave`, `scope` (`code` 또는 `handoff`) |
| `verdict_assigned` | attempt 종료 | `verdict`, `attempt`, `reason`, `next_state` |
| `budget_checkpoint` | 예산 확인 | `tokens`, `cost_usd`, `wall_time_s`, `remaining` |
| `run_finished` | run 종료 | `summary`, `open_debts`, `human_required` |

`classification`은 `green_before | red_before` (baseline) 또는 `green | red` (post)다.

`agent_finished`의 `usage`와 `duration_s`가 11의 비용·속도 지표 전부의 원천이다. **별도 계측 코드를 두지 않는다.**

---

## 파일 배치

```
<repo>/
  .harness/                       # 하네스 전용 control-plane
    config.yaml                   # 하네스가 항상 메인 저장소에서 읽는다
    constitution.md
    approved_commands.yaml
    knowledge/K-###.yaml
    runs/<run-id>/
      manifest.json
      journal.jsonl
      state.json
      tasks/T-003/
        context.manifest.json
        prompt.md
        claim.json      | claim.invalid.json
        handoff.json    | handoff.invalid.json
        verification.json
        review/wave-1/*.json
        transcript.log
  specs/<slug>/spec.yaml
  tasks/T-###.task.yaml
  evals/fixtures/<case>/

<system temp>/harness/<run-id>/<task-id>/
  worktree/                       # agent 의 cwd — 저장소 밖
  outbox/attempt-<n>/
    result.json                   # claim
    handoff.json
    attachments/
```

**워크트리와 outbox는 둘 다 저장소 밖이다.** 워크트리를 `.harness/` 안에 두면 agent의 cwd가 control-plane 안이 되어 "`.harness/`는 agent 영역이 아니다"와 정면충돌한다.

하네스는 `constitution.md`와 `config.yaml`을 **항상 메인 저장소에서 읽고, 워크트리에서는 절대 읽지 않는다.** 워크트리의 사본은 agent가 수정할 수 있는 저장소 콘텐츠이기 때문이다.

`.harness/**`는 전역 `forbidden_paths`에 항상 포함된다.
