# 10 · 실패와 복구

이 문서는 **실패 분류표의 canonical 정의**를 갖는다. 다른 모든 문서는 이 표를 참조하며 재서술하지 않는다.

## 실패 분류 — canonical

verdict와 state는 **다른 축**이다. 03의 정의를 따른다.

| 분류 | 판단 기준 | 예 | verdict | next_state | 재시도 |
|---|---|---|---|---|---|
| **prerequisite** | 시스템은 정상, 외부 준비물이 없다 | CLI 미설치, 로그인 필요, credential 없음, Docker daemon off, 필수 파일·env 없음, 무인 실행 중 미승인 커맨드 | `blocked` | `human_required` | 사람이 해결한 뒤 재개 |
| **system defect** | 하네스·어댑터 구현 문제가 의심된다 | 어댑터 설정 schema 오류, 내부 예외, 프로토콜 위반, 예상 밖 result shape, conformance 위반 | `error` | `ready` → 반복 시 `human_required` | 정책에 따라 제한 재시도 |
| **transient** | 일시적 실행 실패 | 타임아웃, 시그널 kill, 일시적 네트워크 | `error` | `ready` | 백오프 재시도 |
| **verification** | AC·차등 판정 실패 | 회귀, red→green 미달 | `rejected` | 시도 남음 `ready` / 소진 `needs_replan` | 시도 한도까지 |
| **review** | blocking finding 잔존 | | `rejected` | 시도 남음 `ready` / 소진 `needs_replan` | wave 한도까지 |
| **handoff** | 구현은 정상인데 required output 이 없다 | | — | `repairing` | 좁은 fixer, 한도 후 `human_required` |
| **integration** | 머지 충돌 | | — | `integration_conflict` | replan 또는 사람 |
| **task definition** | 재시도가 무의미한 정의 결함 | `policy_denied_at_runtime`, `ac_not_discriminating`, `no_op_detected` | — | `needs_replan` | 없음 |
| **budget** | 예산 소진 | | `budget_exhausted` | `human_required` | 없음 (정지) |

`handoff`, `integration`, `task definition` 세 분류는 **verdict를 기록하지 않는다.** state만 갖는다.

### `blocked`와 `error`의 경계

가장 자주 헷갈리는 두 값이다. 기준은 하나다.

> **사람이 환경을 고치면 해결되는가?** → `blocked`
> **하네스나 어댑터 코드를 고쳐야 하는가?** → `error`

| 상황 | 분류 |
|---|---|
| `claude` CLI 가 PATH 에 없다 | `blocked` (설치하면 해결) |
| 어댑터 설정의 placeholder 를 해석하지 못했다 | `error` (코드·설정 결함) |
| `DATABASE_URL` 이 없다 | `blocked` |
| `AgentResult` 에 필수 필드가 없다 | `error` (프로토콜 위반) |
| Docker daemon 이 꺼져 있다 | `blocked` |
| 무인 실행 중 `require_approval` 커맨드를 만났다 | `blocked` (사람이 승인하면 해결) |
| journal fold 결과가 state 와 다르다 | `error` |

어댑터 `preflight`의 `kind`가 이 경계를 그대로 따른다. 04 참조.

---

## 크래시 일관성

```
1. journal.jsonl 에 이벤트 append
2. fsync
3. state.json 갱신
```

이 순서가 불변이다. 어느 지점에서 죽어도 손실이 없다.

| 죽은 지점 | 결과 |
|---|---|
| 1 이전 | 아무 일도 없었던 것과 같다 |
| 1과 2 사이 | 부분 기록된 마지막 줄은 파싱 실패로 버려진다. 그 이벤트는 없었던 것이 된다 |
| 2와 3 사이 | journal 이 앞서 있다. 재개 시 `last_applied_seq` 이후를 재생하면 복원된다 |
| 3 이후 | 정상 |

- journal의 마지막 줄이 깨져 있으면 **그 줄만 버린다.** 앞선 이벤트는 유효하다.
- `state.json` 자체가 손상되면 journal 전체를 fold해 재구성한다. state는 캐시이므로 언제든 버릴 수 있다.

---

## 재개

```
harness run --resume <run-id>
```

**멱등이어야 한다.** 같은 run-id로 몇 번을 재개해도 결과가 같아야 한다.

| 재개 시점의 state | 처리 |
|---|---|
| `done` (verdict `verified`) | **재실행하지 않는다** |
| `pending`, `ready` | 정상 스케줄링 |
| `precheck`, `running` | agent 실행이 끝나지 않았다. 워크트리·outbox를 정리하고 **그 attempt를 처음부터 다시 시작한다** |
| `executed`, `verifying`, `reviewing`, `repairing` | agent 실행은 끝났다. 워크트리와 outbox가 남아 있으면 **그 attempt의 검증부터 재개한다** — baseline은 journal의 `ac_baseline_executed`로 복원하므로 agent를 다시 부르지 않는다. 남아 있지 않으면 그 attempt를 처음부터 |
| `human_required`, `needs_replan`, `integration_conflict` | 재개하지 않는다. 사람이 조치한 뒤 새 run 또는 명시적 재개 |

죽은 attempt는 **같은 attempt 번호로** 다시 시작한다. 크래시는 재시도 한도를 소진시키지 않는다 — `max_attempts`는 판정이 목표 미달을 보였을 때의 한도이지 프로세스가 죽은 횟수의 한도가 아니다. **그 attempt에 `verdict_assigned`가 없다는 것이 끝나지 않았다는 표시다.**

병렬 실행에서도 규칙은 같다. 처리는 task의 state로 결정되며, 몇 개가 동시에 돌고 있었는지는 재개 판단에 들어가지 않는다. 어느 run에도 속하지 않는 워크트리는 `doctor`가 정리한다.

---

## `harness doctor`

진단과 복구를 한다. 실행해도 안전하다.

| 검사 | 조치 |
|---|---|
| `state == fold(journal)` | 불일치 시 journal 기준으로 state 재구성 |
| journal `seq` 결번·역행 | 보고. 자동 수정하지 않는다 |
| 고아 워크트리 | 어느 run 에도 속하지 않으면 제거 |
| 스테일 락 | pid 생존을 확인하고 죽었으면 해제 |
| 미아 outbox | 승격되지 않은 채 남은 attempt 디렉토리 정리 |
| 어댑터 `preflight` | 등록된 모든 어댑터에 실행하고 **위 분류표 기준으로** prerequisite / system defect 를 구분해 보고 |
| `.harness/` 구조 | 필수 파일 존재 여부 |

`doctor`는 journal을 수정하지 않는다. journal은 불변이다.

---

## `human_required` 런북

사람이 개입해야 할 때 무엇을 보고 어떻게 되돌리는지가 문서에 있어야 한다. 없으면 개입은 추측이 된다.

```
1. 상황 파악
   harness status
   → 어느 task 가 어떤 reason 으로 human_required 인지 확인

2. 증거 확인
   .harness/runs/<run-id>/tasks/<task-id>/verification.json
   .harness/runs/<run-id>/journal.jsonl  (해당 task_id 로 필터)
   → 하네스가 무엇을 관측했는지 확인. claim 은 참고만 한다.

3. reason 별 조치
```

| reason | 확인할 것 | 조치 | 복귀 |
|---|---|---|---|
| prerequisite | `precondition_checked` 이벤트의 실패 항목 | 환경 준비 (설치·로그인·env) | `harness run --resume <run-id>` |
| 미승인 커맨드 | `command_policy_decision` 이벤트 | 커맨드를 검토하고 `.harness/approved_commands.yaml` 에 추가, 또는 task 수정 | `--resume` |
| system defect 반복 | `error` 이벤트의 detail, transcript | 하네스·어댑터 수정 | 수정 후 `--resume` |
| `handoff_missing` | `handoff_rejected` / `handoff_missing` 이벤트 | `outputs.required` 가 타당한지 재검토. 과한 요구면 `optional` 로 내린다 | task 수정 후 `--resume` |
| `needs_replan` | `verdict_assigned` 의 reason | task 정의 수정 (AC 검증력, `allowed_paths`, 커맨드) | `harness analyze` 후 새 run |
| `integration_conflict` | 충돌 파일 목록 | 수동 머지 또는 task 분할 | 해소 후 `--resume` |
| `budget_exhausted` | `budget_checkpoint` 이벤트 | 예산 상향 또는 범위 축소 | 새 run |

---

## 부분 실패에서의 종료

DAG의 일부가 막혀도 **run은 정상 종료한다.** 프로세스를 죽이거나 나머지를 포기하지 않는다.

- `blocked`는 해당 task와 그 하위 의존만 막는다.
- 막히지 않은 가지는 끝까지 진행한다.
- `run_finished` 이벤트와 종료 요약에 무엇이 왜 막혔는지, 무엇이 완료되었는지, open_debts가 무엇인지 적는다.

한 단계의 실패가 런 전체를 중단시키면 사용자는 매번 처음부터 다시 시작해야 한다. 그것은 재개 기능이 있는 것보다 나쁘다.
