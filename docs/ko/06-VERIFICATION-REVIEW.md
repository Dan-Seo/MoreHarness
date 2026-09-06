# 06 · 검증과 리뷰

*이 문서는 [`docs/06-VERIFICATION-REVIEW.md`](../06-VERIFICATION-REVIEW.md)의 번역이다. 둘이 어긋나면 영문이 canonical이다.*

이 문서는 **판정 알고리즘과 Command Policy의 canonical 정의**를 갖는다.

## 판정의 원칙

> Agents propose. Harness verifies. **Evidence decides.**

판정에 쓰이는 것은 하네스가 직접 만든 관측치뿐이다. agent의 claim, exit code, 산출물의 존재 여부는 그 자체로 결론이 되지 못한다.

---

## Command Policy

> **Agent-authored commands are untrusted input. Harness must authorize them before execution.**

task 정의는 planner agent가 쓸 수 있고, 저장소 콘텐츠는 오염될 수 있다. 하네스가 실행하는 모든 커맨드는 실행 전에 정책을 통과해야 한다.

### 적용 대상

- acceptance criteria 커맨드
- `kind: command` precondition
- 프로젝트 health 커맨드 (`converge`가 실행하는 것)
- verifier·plugin이 실행하려는 커맨드
- eval fixture가 실행하려는 커맨드

**예외는 없다.** 하네스가 서브프로세스를 띄우는 모든 지점이 `policy.py`를 통과한다.

### 설정

```yaml
command_policy:
  default: require_approval          # fail-closed
  rules:                             # 첫 매치 우선
    - {match: '^(npm|pnpm|yarn) (test|run (build|lint|typecheck))$', verdict: allow}
    - {match: '^(pytest|python -m pytest)', verdict: allow}
    - {match: '^git (status|diff|log)', verdict: allow}
    - {match: 'rm\s+-rf|git\s+push\s+--force|git\s+reset\s+--hard|^sudo|DROP\s+DATABASE|id_rsa', verdict: deny}
    - {match: '^(curl|wget|npm install|pip install|terraform apply|kubectl apply)', verdict: require_approval}
```

매칭 대상은 argv를 공백으로 join한 정규화 문자열이다.

`harness init`이 이 규칙 목록을 기본 config에 써 넣는다. 무엇이 자동 실행 승인되었는지는 파일을 열어 보면 알 수 있어야 하고, 프로젝트는 그것을 검토하고 고친다.

`command_policy` 키가 아예 없으면 규칙이 하나도 없는 것이므로 모든 커맨드가 `default`를 받는다. **fail-closed는 키의 부재에도 그대로 적용된다.**

### 판정 순서

```
1. argv 를 정규화 문자열로 만든다
2. rules 를 위에서부터 훑어 첫 매치의 verdict 를 채택한다
3. 매치가 없으면 default 를 채택한다 (fail-closed = require_approval)
4. command_policy_decision 이벤트를 남긴다
5. verdict 별 처리
```

| verdict | 처리 |
|---|---|
| `allow` | 실행한다 |
| `deny` | 실행하지 않는다. `analyze`가 사전에 잡았어야 하므로, 런타임 도달은 task 정의 결함이다 → state `needs_replan` (reason: `policy_denied_at_runtime`) |
| `require_approval` | TTY 면 대화형 승인. 아니면 `.harness/approved_commands.yaml` 조회. 무인 실행에서 미승인이면 verdict `blocked` |

### 승인 저장 형식

```yaml
# .harness/approved_commands.yaml
approvals:
  - cmd: ["npm", "install"]
    hash: "sha256:..."        # 정규화 문자열의 해시
    approver: "emdhks09@gmail.com"
    approved_at: "2026-08-27T14:20:00+09:00"
    scope: run                # run | project
```

해시가 키이므로 인자가 하나라도 바뀌면 승인이 재사용되지 않는다.

### 기본 실행은 `shell=False`

argv 리스트를 그대로 `subprocess.run(argv, shell=False)`에 넘긴다. 셸 메타문자, 명령 치환, 파이프, 리다이렉션이 해석되지 않으므로 문자열 매칭을 우회하는 가장 흔한 경로가 닫힌다.

셸이 반드시 필요한 커맨드는 `shell: true`를 명시해야 하고, **그 선언 자체가 자동으로 `require_approval`로 승격된다.**

### 정직한 한계

**정규식 매칭은 보안 경계가 아니다.**

이것은 잘못된 planner와 저장소 인젝션에 대한 가드레일이다. 진짜 경계는 실행 프로파일이며, 그것은 `container`뿐이다. 05의 보장/미보장 표를 참조한다.

`allow`의 의미와 그 한계는 09에 서술한다.

---

## Acceptance Criteria 생명주기

```
[1] baseline   agent 실행 전, 하네스가 직접 실행한다
[2] agent 실행
[3] post       모든 AC 를 다시 실행한다
```

### baseline

각 AC를 실행해 `green_before` 또는 `red_before`로 분류하고 `ac_baseline_executed` 이벤트를 남긴다.

| 상황 | 처리 |
|---|---|
| `expect_fail_before: true` 인데 `green_before` | **`ac_not_discriminating`.** 무엇을 바꾸든 통과하므로 검증력이 없다. **agent 를 실행하지 않고** state `needs_replan` |
| `expect_fail_before` 가 아닌데 `red_before` | `pre_existing_failure`. `debt_opened` 이벤트로 원장에 올린다 |

`expect_fail_before`는 red→green 증명을 요구하는 선언이다. baseline에서 이미 통과한다면 그 AC는 이 task에 대해 아무것도 증명하지 못한다.

### 실행 환경

- cwd는 워크스페이스(워크트리)다. baseline과 post가 같은 cwd를 쓴다.
- 타임아웃은 config의 `ac_timeout_s`(기본 300). 초과는 `red`로 분류하고 사유를 기록한다.
- exit code 0 = green, 그 외 = red. AC는 하네스가 실행하므로 여기서는 exit code가 곧 관측치다.
- 모든 AC 커맨드는 Command Policy를 통과한다.

---

## TDD 모드 — red→green 을 하네스가 관측한다

`development.mode: tdd`(03)인 task는 한 attempt 안에서 **두 번 디스패치**된다. agent가 "TDD로 했다"고 말하는 것은 증거가 아니므로, 하네스가 단계 사이에 직접 관측한다.

```
[1] baseline        expect_fail_before 가 아닌 AC 만 실행한다
[2] test-author     첫 디스패치 — 테스트만 쓴다
[3] red gate        expect_fail_before AC 의 baseline 을 여기서 잰다. 전부 red 여야 한다
[4] implementation  둘째 디스패치 — 구현만 한다
[5] post            모든 AC 를 다시 실행한다 (위 차등 판정 그대로)
```

**`expect_fail_before` AC의 baseline은 [3]에서 잰다.** 테스트가 없는 시점의 red는 아무것도 증명하지 못한다. 테스트가 존재하고 실패한다는 관측만이 red→green의 before다. baseline이 두 조각으로 나뉘지만 합집합은 여전히 AC 하나당 관측 하나이므로, 차등 판정도 재개(10)도 표준 모드와 같은 절차다.

**red gate를 통과하지 못하면 implementation을 디스패치하지 않는다.**

### 단계 스코프

각 단계의 diff는 그 단계의 관측 기준에 대해 계산되고 05의 경로 스코프 판정을 그대로 받는다.

| 단계 | 관측 기준 | `allowed` | `effective_forbidden` 에 더해지는 것 |
|---|---|---|---|
| test-author | dispatch 시점 HEAD (`task_dispatched` 의 `base`) | `test_paths` | `implementation_paths` |
| implementation | red gate 커밋 (`tdd_phase_completed` 의 `base`) | `implementation_paths` | `test_paths` |

- 두 목록이 겹치게 선언되어도 각 단계에서 **상대 목록이 금지**이므로 구멍이 생기지 않는다.
- test-author 단계가 끝나면 하네스가 그 변경을 task 브랜치에 커밋하고 그 sha를 다음 단계의 관측 기준으로 남긴다. 이후 관측이 이 sha 대비이므로 **agent가 커밋으로 변경을 감춰도 같은 diff로 관측된다.** 테스트의 수정·삭제가 implementation 단계의 `path_violation`이 되는 것이 여기서 나온다.
- attempt 전체의 diff 판정(`kind` 기대·`allowed_paths`·리뷰 티어)은 두 단계의 합에 대해 그대로 한다. **TDD는 증거를 더할 뿐 기존 판정을 대체하지 않는다.** fixer가 만든 변경도 같은 단계 스코프를 다시 통과해야 한다.

### 게이트 실패

| 상황 | reason | verdict | next_state |
|---|---|---|---|
| test-author 가 스코프를 벗어났다 (구현 경로 포함) | `path_violation` | `rejected` | 시도 규칙대로 |
| test-author 가 아무것도 쓰지 않았다 | `tdd_no_tests` | `rejected` | 시도 규칙대로 |
| red gate 에서 AC 가 green | `ac_not_discriminating` | — | `needs_replan` |
| `expect_fail_before` AC 가 하나도 없다 | `ac_not_discriminating` | — | `needs_replan` |
| red gate 의 AC 가 `deny` | `policy_denied_at_runtime` | — | `needs_replan` |
| red gate 의 AC 가 미승인이라 실행되지 않았다 | `unapproved_command` | `blocked` | `human_required` |
| red gate 의 AC 가 실행되지 못했다 (타임아웃·프로세스 실패) | `tdd_red_gate_unexecuted` | `error` | 시도 규칙대로 |
| implementation 이 테스트를 고쳤다·지웠다, 또는 스코프를 벗어났다 | `path_violation` | `rejected` | 시도 규칙대로 |
| implementation 뒤에도 red | `unmet` | `rejected` | 시도 규칙대로 |
| 기존 green AC 가 red 가 됐다 | `regression` | `rejected` | 시도 규칙대로 |
| test-author 변경을 red 기준 커밋으로 고정하지 못했다 | `tdd_checkpoint_failed` | `error` | 시도 규칙대로 |
| 워크트리 분리가 없는 프로파일 (`safe`) | `tdd_requires_worktree` | `error` | 시도 규칙대로 |
| TDD 모드인데 그 단계를 실행할 옵션이 설치본에 없다 | `tdd_mode_unavailable` | `error` | 시도 규칙대로 |

`ac_not_discriminating`을 그대로 쓰는 것은 의미가 같기 때문이다 — 테스트가 존재하는데도 통과하는 AC는 무엇을 바꾸든 통과하므로 검증력이 없다. 새 state를 만들 이유가 없다.

`tdd_requires_worktree`와 `tdd_mode_unavailable`이 `error`인 것은 10의 경계를 따른다. 사람이 환경을 준비해서 풀리는 문제가 아니라 task 선언과 설치본의 조합이 성립하지 않는 설정 결함이다.

### 증거의 소재

| 질문 | journal |
|---|---|
| test-author 단계가 끝났는가 | `tdd_phase_completed {phase: test_author, base}` |
| red gate 를 통과했는가 | `tdd_phase_completed {phase: red_gate, ok}` |
| 무엇이 red 증거였는가 | 그 사이의 `ac_baseline_executed {classification: red_before}` |
| implementation 을 실행했는가 | `agent_finished` 뒤의 `tdd_phase_completed {phase: implementation, ok: true}` |
| green gate 결과는 무엇인가 | `ac_post_executed {differential: proven \| unmet}` |

green gate를 위한 이벤트는 따로 두지 않는다. 차등 판정의 결과가 곧 green gate이며, 지표는 journal의 projection이지 별도 계측이 아니다 (03).

---

## 정규화 — 판정 이전 단계

```
outbox 의 raw claim / raw handoff 발견
        │
        ├ jsonschema 검증 통과 → claim.json / handoff.json 으로 승격
        │                        claim_received / handoff_received 이벤트
        │
        └ 검증 실패          → *.invalid.json 으로 보존
                               claim_rejected / handoff_rejected 이벤트
                               이후 판정에서 None 으로 취급
```

> **Invalid claim is an invalid report, not automatically an invalid implementation.**

존재하는 산출물을 읽거나 UTF-8로 디코딩할 수 없으면, 정규화 단계는 이를 승격하거나
run을 중단하지 않고 원본 경로와 읽기 오류를 `*.invalid.json`에 진단 정보로 기록한다.
원본은 건드리지 않는다. 다른 invalid 보고와 마찬가지로 판정이나 required handoff
게이트에 쓸 수 있는 payload는 없는 것으로 취급한다.

정규화는 판정이 아니다. 여기서 verdict가 결정되는 경로는 없다.

---

## 차등 판정 — task는 자기가 바꾼 것으로만 평가된다

| baseline | post | task 판정 |
|---|---|---|
| green | green | 정상 |
| green | red | **회귀 → `rejected`** |
| red (`expect_fail_before`) | green | **red→green 증명 성공** |
| red (`expect_fail_before`) | red | 목표 미달 → `rejected` |
| red (pre-existing) | red | 이 task 의 책임이 아니다. **판정에 영향 없음.** debt 유지 |
| red (pre-existing) | green | 부수적으로 해결됨 → `debt_closed` |

**무관한 기존 실패 때문에 올바른 task가 rejected되지 않는다.**

예외가 하나 있다. pre-existing 실패가 그 task의 `allowed_paths` 안에 있다면 면제하지 않고 리뷰 finding으로 승격한다. 자기 영역의 깨진 테스트를 모른 척하는 것은 정상 작업이 아니다.

---

## verified 조건

다음 네 가지가 **전부** 통과해야 한다. **claim과 handoff는 여기에 없다.**

1. `kind`에 맞는 diff 기대 충족 — `implementation`은 비어 있지 않음, `readonly`는 비어 있음, `analysis`는 무관
2. 변경이 `allowed_paths` 안이고 `effective_forbidden` 밖 (05 참조)
3. 위 차등 판정표에서 `rejected` 사유가 없음
4. `effective_risk` 티어가 요구하는 리뷰의 blocking finding이 0

diff 의 관측 기준은 **dispatch 시점의 HEAD**(`task_dispatched` 의 `base`)다. agent 가 변경을 커밋했든 워킹트리에 남겼든 같은 diff 로 관측된다 — 커밋 여부는 판정에 영향을 주지 않는다 (05).

**claim이 깨졌거나 없다는 이유만으로 rejected되는 경로는 존재하지 않는다.**

`implementation` task인데 diff가 비었고 AC가 전부 통과하면 조용히 통과시키지 않는다. `no_op_detected`로 기록하고 state `needs_replan`으로 보낸다. 이 조합은 AC에 검증력이 없다는 신호다.

---

## terminal 단계 — handoff 게이트와 통합

증거 조건 1~4를 통과했다는 이유만으로 즉시 `verified`를 기록하지 않는다. **`verified`는 terminal에서만 기록한다.** 순서는 고정이다.

```
증거 조건 1~4 통과
   │  (아직 verdict 미기록)
   ▼
required handoff gate
   ├ outputs.required 가 비어 있음        → 통합으로
   ├ required handoff 가 유효             → 통합으로 (TaskOutput 병합 기록)
   └ required handoff 누락 또는 invalid   → verdict 미기록, next_state = repairing
   │
   ▼
통합  (워크트리를 쓰는 프로파일에 한한다. 브랜치 구조는 05)
   ├ 머지 성공                            → verdict = verified
   └ 머지 충돌                            → verdict 미기록, next_state = integration_conflict
```

**`verified`가 기록되는 지점은 이 마지막 한 곳뿐이다.** 그래서 `verdict_assigned`가 attempt당 한 번이라는 03의 규칙이 유지된다. 머지를 verdict 뒤에 두면 충돌 시 같은 attempt에 두 번째 verdict를 기록해야 한다.

`repairing`에서 하는 일:

- **코드는 그대로 둔다.** 되돌리지도 다시 만들지도 않는다. 구현은 이미 증거로 검증되었다.
- **handoff artifact만** 재생성하는 좁은 fixer를 호출한다. `fixer_dispatched {scope: "handoff"}`.
- 기존 fixer 경로와 프롬프트 템플릿 하나를 공유한다. 새 기계장치를 만들지 않는다.
- 성공 → verdict `verified` → `done`.
- 시도 소진 → state `human_required` (reason: `handoff_missing`).

`optional` output이 없으면 다음 task의 컨텍스트에서 그냥 제외한다. 아무 판정도 하지 않는다.

---

## exit code

> `exit_code == 0` is not success. `exit_code != 0` is not necessarily implementation failure. **Evidence decides.**

| 상황 | 처리 |
|---|---|
| timeout / 시그널 kill / 프로토콜 위반 | 실행 자체가 유효하게 성립하지 않았다. `AgentResult.runtime_failure`로 표시되고 verdict `error`로 분류되어 재시도 정책을 탄다 |
| 평범한 non-zero exit | **증거 하나일 뿐이다.** `agent_exit_nonzero` 이벤트로 exit code와 stderr 꼬리를 남기되, 하네스 소유 증거가 전부 통과하면 `verified`가 가능하다 |
| exit 0 | 성공의 근거가 아니다. 판정은 위 알고리즘으로만 한다 |

`verified_with_warning` 같은 파생 verdict를 만들지 않는다. non-zero exit는 journal에 남고 리포트에 표시되지만 verdict를 오염시키지 않는다.

---

## blocked 판정 — 하네스 소유 증거만

```
1. precheck
   하네스가 preconditions 와 어댑터 preflight 를 직접 실행한다.
   missing_prerequisite       → verdict blocked (agent 미실행)
   misconfigured / internal   → verdict error

2. 실행 후 실패의 재분류
   environment verifier 가 하네스 소유 증거로 판단한다.
   · AC stderr 를 config 의 blocked_signals 패턴과 대조
   · preconditions 를 다시 실행
   · 독립 probe 로 확인되면 → verdict blocked

3. claim 의 blocked_hint
   해당 probe 를 우선 실행하는 트리거로만 쓴다.
   probe 가 통과하면 힌트를 인정하지 않는다
   → verdict rejected + claim_uncorroborated 이벤트
```

**어떤 경로로도 agent의 말만으로 `blocked`에 도달할 수 없다.**

`blocked`는 해당 task와 그 하위 의존만 막는다. 런 전체를 중단하지 않는다.

`blocked`와 `error`의 canonical 경계는 10의 실패 분류표다.

---

## effective_risk

```
effective_risk = max(declared_risk, path_floor, diff_floor)
```

- `path_floor` — `risk_rules`의 경로 패턴(05의 glob 방언)에서 나온다. 사전에는 `allowed_paths`에, 사후에는 실제 diff 경로에 적용한다.
- `diff_floor` — 실제 diff의 규모에서 나온다. **변경 라인 합이 400 이상이거나 변경 파일이 20개 이상이면 `medium`.** 신규 의존성·공개 API 표면 같은 신호는 경로로 표현되는 한 `risk_rules`가 잡는다 — 의존성 매니페스트가 기본 목록에 있는 이유다.
- `risk`를 선언하지 않은 task의 declared는 `trivial`로 본다. floor가 안전망이다.

**2단계로 계산한다.**

1. **사전** — 선언값과 `allowed_paths`로 floor를 잡아 스케줄링과 예산을 결정한다.
2. **사후** — **실제 diff를 본 뒤 리뷰 티어를 확정한다.**

사전값만 쓰면 `trivial`로 선언한 task가 인증 코드를 건드려도 리뷰를 빠져나간다. 상향만 가능하고, 하향은 기록되는 사람 waiver로만 한다. 상향은 `risk_escalated` 이벤트로 남는다.

### risk_rules

```yaml
risk_rules:                      # 선언은 내장 기본 목록에 **추가**된다
  - {match: "src/payments/**", floor: high}
```

내장 기본 목록 — 인증·암호·비밀·마이그레이션·CI 워크플로·컨테이너 정의·의존성 매니페스트:

```yaml
- {match: "**/auth/**", floor: high}
- {match: "**/*secret*", floor: high}
- {match: "**/*password*", floor: high}
- {match: "**/*credential*", floor: high}
- {match: "**/migrations/**", floor: high}
- {match: ".github/workflows/**", floor: high}
- {match: "**/Dockerfile*", floor: high}
- {match: "**/docker-compose*", floor: high}
- {match: "**/requirements*.txt", floor: high}
- {match: "**/pyproject.toml", floor: high}
- {match: "**/package.json", floor: high}
- {match: "**/go.mod", floor: high}
- {match: "**/Cargo.toml", floor: high}
```

선언으로 기본을 **끌 수 없다.** effective_risk는 전체 목록의 최대값이므로 추가는 상향만 만든다. 하향은 기록되는 사람 waiver뿐이다.

### 티어

| 티어 | 리뷰어 |
|---|---|
| `trivial` | 없음 — 검증만 |
| `low` | `spec` |
| `medium` | + `quality` |
| `high` | + `architecture-security` |
| `critical` | + `adversarial` |

---

## Bounded review wave

`max_review_waves` 기본값은 2다 (06의 config 키).

```
wave n:
  티어의 리뷰어를 각각 독립 실행 (fresh context — 병렬 여부는 구현 세부다)
  findings 병합 + 중복 제거 (키: rule · file · line)
  blocking finding 이 0 이면 → handoff 게이트로
  아니면 fixer 1명 호출 (fresh context: findings + 해당 파일 + task 계약만)
  AC post 재실행 + diff·경로 재판정 — 리뷰가 만든 변경도 같은 증거 기준을 통과해야 한다
  다음 wave 에서 재리뷰
wave 한도 초과 후에도 blocking 이 남으면 → verdict rejected (10 의 review 행)
```

- **리뷰어는 구현자의 대화를 보지 않는다.** 컨텍스트는 diff, task 계약, constitution뿐이다. 구현자의 논리에 설득당하는 리뷰는 독립 리뷰가 아니다.
- `blocking` 판정은 결정론적이다: `severity ∈ {high, critical}` 또는 `rule ∈ constitution.critical`. 리뷰어가 스스로 blocking 여부를 정하지 않는다. **constitution의 `## critical` 섹션의 리스트 항목이 그 rule 목록이다.**
- 예산 초과 시 조용히 품질 기준을 낮추지 않는다. verdict `budget_exhausted`로 정지한다.

### 리뷰어의 산출물 — findings

리뷰어는 agent이며, 자기 outbox에 `findings.json`을 쓴다.

```json
{"schema": "harness.findings/v1", "task_id": "T-003",
 "findings": [{"severity": "high", "rule": "hardcoded-secret",
               "file": "src/db.py", "line": 12, "message": "비밀이 코드에 있다"}]}
```

- `severity`는 `info | low | medium | high | critical`.
- **findings 파일이 없거나 schema를 위반하면 그 리뷰는 성립하지 않는다** — verdict `error` (10의 system defect). 리뷰어의 침묵을 통과로 해석하지 않는다. 발견이 없으면 빈 배열을 쓴다.
- 각 wave의 원본은 `review/wave-<n>/<reviewer>.json`으로 보존된다 (03의 파일 배치).

---

## 예산 상한

run이 쓸 수 있는 자원의 상한이다. **초과 시 조용히 품질 기준을 낮추지 않는다** — 그 시점의 task에 verdict `budget_exhausted`를 부여하고, 이후 task도 같은 확인에 걸려 run이 멈춘다. run 자체는 정상 종료하고 요약에 남는다 (10).

```yaml
budget:
  max_wall_time_s: null       # run 시작부터의 벽시계 시간. null 은 무제한
  max_agent_calls: null       # agent 프로세스 호출 수 — 리뷰어·fixer 포함
  max_cost_usd: null          # usage 를 보고하는 어댑터에서만 유효. 추정하지 않는다 (11)
```

- 확인 지점은 **모든 agent 프로세스를 부르기 직전**이다 — TDD의 두 디스패치, 리뷰어,
  fixer, handoff repair도 각각 별도로 확인한다.
- 확인할 때마다 `budget_checkpoint` 이벤트가 남는다. 소비량은 전부 journal의 projection이다 — 별도 카운터가 없다.
- 상한이 하나도 설정되지 않았으면 확인하지 않고 이벤트도 남기지 않는다.

---

## 남는 최대 리스크

**AC가 부실하면 하네스도 진실을 알 수 없다.** 하네스는 AC보다 똑똑해질 수 없다.

완화는 셋이다.

1. baseline의 검증력 체크 (`ac_not_discriminating`)
2. `analyze`가 **코드가 아니라 AC 자체를 리뷰**한다 (08)
3. 11의 `escape_rate`로 이 리스크를 **수치화**한다 — 하네스가 `verified`라고 했는데 hidden grader는 실패로 본 비율

---

## 06이 소유하는 config 키

03의 `config.yaml` canonical은 최상위 키의 뼈대만 정한다. 아래 키의 정의는 이 문서가 갖는다.

```yaml
ac_timeout_s: 300             # AC 한 개의 타임아웃(초)
agent_timeout_s: 1800         # agent 프로세스 하나의 타임아웃(초)

max_attempts: 2               # 한 task 가 rejected/error 로 재시도할 수 있는 횟수
max_handoff_repairs: 1        # repairing 에서 handoff fixer 를 부를 수 있는 횟수

blocked_signals: []           # AC stderr 대조 패턴(정규식) 목록

max_review_waves: 2           # bounded review wave 의 한도
adversarial_adapter: null     # adversarial 리뷰어가 쓸 어댑터 이름. null 이면 task 의 어댑터

risk_rules: []                # effective_risk 의 경로 floor — 위 "risk_rules" 절

budget:                       # 위 "예산 상한" 절
  max_wall_time_s: null
  max_agent_calls: null
  max_cost_usd: null

command_policy:               # 위 "Command Policy" 절의 형태
  default: require_approval
  rules: []
```

- `agent_timeout_s`가 `AgentRequest.timeout_s`의 출처다. 초과는 어댑터가 `runtime_failure=timeout`으로 표시하며, 04가 canonical이다.
- **`max_attempts`가 03의 verdict → next_state 표에서 말하는 "시도 소진"의 기준이다.** 소진되면 `rejected`의 next_state가 `ready`가 아니라 `needs_replan`이 된다.
- `max_handoff_repairs`를 소진하면 state `human_required`(reason: `handoff_missing`)다.
- **`adversarial_adapter`는 `critical` 티어의 독립성을 한 단계 더 올리는 옵션이다.** 구현자와
  같은 모델이 자기 결과를 적대적으로 검토하면 같은 맹점을 공유한다. `adapters`에 선언된 다른
  이름을 지정하면 `adversarial` 리뷰어만 그 어댑터로 실행된다. 나머지 리뷰어와 fixer 는 영향을
  받지 않는다. 지정한 이름이 `adapters`에 없으면 로드 실패다.
- **`blocked_signals`의 기본이 빈 목록인 것은 의도다.** 패턴을 미리 심으면 프로젝트마다 오탐이 생기고, 오탐의 결과는 잘못된 `blocked`다. 기본 경로는 precondition 재실행이라는 하네스 소유 증거이며, 패턴은 그 저장소가 자기 실패 양상을 알 때 더한다.
