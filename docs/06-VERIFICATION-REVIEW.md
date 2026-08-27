# 06 · 검증과 리뷰

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

**claim이 깨졌거나 없다는 이유만으로 rejected되는 경로는 존재하지 않는다.**

`implementation` task인데 diff가 비었고 AC가 전부 통과하면 조용히 통과시키지 않는다. `no_op_detected`로 기록하고 state `needs_replan`으로 보낸다. 이 조합은 AC에 검증력이 없다는 신호다.

---

## handoff 게이트 — 구현 판정과 분리

증거 조건 1~4를 통과했다는 이유만으로 즉시 `verified`를 기록하지 않는다. **`verified`는 terminal에서만 기록한다.** 순서는 고정이다.

```
증거 조건 1~4 통과
   │  (아직 verdict 미기록)
   ▼
required handoff gate
   ├ outputs.required 가 비어 있음        → verdict = verified
   ├ required handoff 가 유효             → verdict = verified (TaskOutput 병합 기록)
   └ required handoff 누락 또는 invalid   → verdict 미기록, next_state = repairing
```

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

- `path_floor` — config의 `risk_rules` 경로 패턴에서 나온다. 인증·암호·비밀·마이그레이션·CI 워크플로·컨테이너 정의·의존성 매니페스트는 `high`.
- `diff_floor` — 변경 규모, 신규 의존성 추가, 공개 API 표면 변경에서 나온다.

**2단계로 계산한다.**

1. **사전** — 선언값과 `allowed_paths`로 floor를 잡아 스케줄링과 예산을 결정한다.
2. **사후** — **실제 diff를 본 뒤 리뷰 티어를 확정한다.**

사전값만 쓰면 `trivial`로 선언한 task가 인증 코드를 건드려도 리뷰를 빠져나간다. 상향만 가능하고, 하향은 기록되는 사람 waiver로만 한다. 상향은 `risk_escalated` 이벤트로 남는다.

### 티어

| 티어 | 리뷰 |
|---|---|
| `trivial` | 검증만 |
| `low` | + spec 준수 |
| `medium` | + 코드 품질 |
| `high` | + 아키텍처·보안 |
| `critical` | + cross-adapter adversarial |

---

## Bounded review wave

`MAX_REVIEW_WAVES` 기본값은 2다.

```
wave n:
  티어별 리뷰어를 병렬 실행
  findings 병합 + 중복 제거
  blocking finding 이 0 이면 → handoff 게이트로
  아니면 fixer 1명 호출 (fresh context: findings + diff + task 계약만)
  AC post 재실행
  scoped 재리뷰 (고쳐진 부분에 한정)
wave 한도 초과 후에도 blocking 이 남으면 → replan 1회 → state human_required
```

- **리뷰어는 구현자의 대화를 보지 않는다.** 컨텍스트는 diff, task 계약, constitution뿐이다. 구현자의 논리에 설득당하는 리뷰는 독립 리뷰가 아니다.
- `blocking` 판정은 결정론적이다: `severity >= high` 또는 `rule ∈ constitution.critical`. 리뷰어가 스스로 blocking 여부를 정하지 않는다.
- 예산 초과 시 조용히 품질 기준을 낮추지 않는다. verdict `budget_exhausted`로 정지한다.

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

command_policy:               # 위 "Command Policy" 절의 형태
  default: require_approval
  rules: []
```

- `agent_timeout_s`가 `AgentRequest.timeout_s`의 출처다. 초과는 어댑터가 `runtime_failure=timeout`으로 표시하며, 04가 canonical이다.
- **`max_attempts`가 03의 verdict → next_state 표에서 말하는 "시도 소진"의 기준이다.** 소진되면 `rejected`의 next_state가 `ready`가 아니라 `needs_replan`이 된다.
- `max_handoff_repairs`를 소진하면 state `human_required`(reason: `handoff_missing`)다.
- **`blocked_signals`의 기본이 빈 목록인 것은 의도다.** 패턴을 미리 심으면 프로젝트마다 오탐이 생기고, 오탐의 결과는 잘못된 `blocked`다. 기본 경로는 precondition 재실행이라는 하네스 소유 증거이며, 패턴은 그 저장소가 자기 실패 양상을 알 때 더한다.
