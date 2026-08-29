# 08 · 수렴과 학습

이 문서는 **ship 게이트와 debt 원장의 canonical 정의**를 갖는다.

두 개의 게이트가 있다. 하나는 구현 전에, 하나는 구현 후에 선다.

```
tasks  →  analyze  →  run  →  converge  →  ship
          (사전)              (사후)
```

---

## 저작 단계 — spec · clarify · plan · tasks

이 단계들의 산출물은 사람(또는 사람이 시킨 agent)이 채운다. 하네스가 하는 일은 **형태를 만들고(스캐폴드), 정합성을 검사(analyze)하는 것**이다. 내용의 품질을 하네스가 만들어 주지 않는다.

- `harness spec "<intent>"` — `specs/<slug>/spec.yaml`을 템플릿에서 만든다. slug는 intent에서 만들거나 `--slug`로 준다. R-### 골격과 `[NEEDS CLARIFICATION]` 항목이 들어 있다.
- `harness clarify` — 남은 `[NEEDS CLARIFICATION]`을 보여 준다. TTY면 하나씩 답을 받아 `clarifications:`로 옮기고 항목을 지운다. 남아 있는 한 종료 코드는 0이 아니다.
- `harness plan` — spec마다 `specs/<slug>/plan.md`를 템플릿에서 만든다.
- `harness tasks` — 어떤 task도 만족시키지 않는 R-###마다 task 골격을 만든다. `spec_hash`는 이때 하네스가 찍는다 (03). acceptance는 비워 둔다 — 채우기 전에는 analyze가 막는다.

이미 있는 파일은 덮어쓰지 않는다.

---

## `harness analyze` — 구현 전 게이트

**코드는 보지 않는다.** spec, plan, task 정의의 정합성만 본다.

### 검사 항목

**요구사항 추적**
- `satisfies`가 실재하는 R-###를 가리키는가
- 모든 R-###가 최소 하나의 task에 할당되었는가
- 어떤 R-###도 만족시키지 않는 고아 task가 있는가

**DAG**
- 순환이 있는가
- 존재하지 않는 task를 `depends_on`하는가
- `max_parallel > 1`일 때 동시 실행 가능한 task 사이에 `allowed_paths` 충돌이 있는가

**Acceptance Criteria**
- 모든 task에 AC가 있는가
- AC가 실행 가능한 형태인가 (argv 리스트, 존재하는 실행 파일)
- **AC에 검증력이 있는가** — 이것이 가장 중요한 검사다. 코드가 아니라 **AC 자체를 리뷰**한다. `expect_fail_before` 없이 회귀만 잡는 AC뿐인 task, 무엇을 해도 통과하는 AC를 지적한다

**Command Policy 사전 검출**
- 모든 AC 커맨드와 `kind: command` precondition에 Command Policy를 적용한다
- `deny` 매치는 **실패로 보고하고 `run`을 막는다**
- `require_approval` 매치는 보고하여 사람이 미리 승인하거나 task를 고치게 한다

**outputs**
- `outputs.required`에 하네스 산출 필드(`changed_files` 등)를 잘못 지정하지 않았는가
- `required`로 선언한 필드를 실제로 downstream task가 쓰는가

**드리프트와 미해소**
- `[NEEDS CLARIFICATION]`이 남아 있는가
- task의 `spec_hash`가 현재 spec과 일치하는가

### 실패와 경고

analyze의 지적은 두 급이다.

**실패 — `run`을 막는다**

- `satisfies`가 존재하지 않는 R-###를 가리킨다
- 할당되지 않은 R-###가 있다
- DAG에 순환이 있거나 존재하지 않는 task에 의존한다
- `implementation` task에 AC가 없다
- AC가 argv 리스트가 아니거나 실행 파일을 찾을 수 없다
- Command Policy `deny` 매치
- `outputs.required`에 하네스 산출 필드가 있다
- `[NEEDS CLARIFICATION]` 잔존 (spec 텍스트 어디에 있든)
- `spec_hash` 불일치 (드리프트)

**경고 — 보고하되 막지 않는다**

- `expect_fail_before` 없는 AC뿐인 implementation task (검증력 의심)
- Command Policy `require_approval` 매치 — 사람이 미리 승인할 기회
- 어떤 R-###도 만족시키지 않는 고아 task
- `required`를 선언했는데 그것을 소비할 downstream이 없다
- `spec_hash`가 없어서 드리프트를 검출할 수 없다
- `max_parallel > 1`인데 병렬 가능한 task의 `allowed_paths`가 겹친다 — 실행은 안전하다(직렬화된다, 05). 병렬성을 잃을 뿐이다

requirement 추적 검사 중 "할당되지 않은 R"과 "고아 task"는 `specs/`가 있을 때만 의미가 있다. spec 없이 task만 있는 저장소에서는 공허하게 통과한다.

### 산출과 게이트

- `.harness/analyze-report.md` — 사람용 보고
- `.harness/analyze.json` — `{ok, fingerprint, generated_at}`. fingerprint는 `specs/`와 `tasks/` 파일 내용의 해시다.

`harness run`은 `analyze.json`이 **있을 때** 그것을 게이트로 쓴다 — `ok: false`면 막고, fingerprint가 현재 파일들과 다르면 어느 쪽이든 "analyze를 다시 실행하라"로 막는다. `analyze.json`이 없으면 게이트를 쓰지 않는 저장소다 — run은 진행한다. 커널은 analyze 없이도 동작해야 하기 때문이다 (02의 옵션 경계).

---

## `harness converge` — 구현 후 게이트

**"구현이 끝났다" ≠ "요구사항이 다 구현됐다".**

### 커버리지 매트릭스

```
R-###  →  task  →  verdict  →  evidence
```

| 상태 | 의미 |
|---|---|
| covered | 요구사항을 만족시키는 task가 있고 verdict가 `verified`다 |
| uncovered | 어떤 task도 이 R-###를 `satisfies`하지 않는다 |
| unverified | task는 있으나 `verified`에 도달하지 못했다 |
| partial | 여러 task 중 일부만 `verified`다 |

### 그 밖의 검사

- **드리프트** — task의 `spec_hash`와 현재 spec의 해시가 다르다. 구현 중에 스펙이 바뀌었다는 뜻이다.
- **고아 diff** — verified task의 `allowed_paths`로 설명되지 않는 변경이 통합 브랜치에 있다. `allowed_paths`를 선언하지 않은 task는 제한이 없으므로 모든 변경을 설명한다 (05).
- **전체 AC 합집합 실행** — 통합 브랜치를 하네스 소유 임시 워크트리로 꺼내, 모든 task의 AC와 config의 `health_commands`를 실행한다. Command Policy를 통과한다. red가 open debt에 해당하면 원장 항목으로 보고하고, **그 밖의 red는 실패다.**
- **open_debts** — 원장을 보고에 싣는다. **debt는 converge의 실패 조건이 아니라 ship의 조건이다.** 차등 판정이 task를 면제한 것을 converge가 다시 벌하면 두 층위의 분리가 무너진다.

converge가 실패하는 조건 — uncovered/unverified/partial이 있다 · 드리프트가 있다 · 고아 diff가 있다 · debt에 해당하지 않는 red가 있다.

### 산출

- `.harness/coverage.md` — 사람용 커버리지 매트릭스
- `.harness/converge.json` — `{ok, run_id, integration, coverage, ...}`. ship이 이것을 읽는다.

### 08이 소유하는 config 키

```yaml
health_commands: []           # converge 가 AC 합집합에 더해 실행하는 argv 리스트 목록
```

---

## debt 원장

task 판정은 **차등적**이고 ship 게이트는 **절대적**이다. 두 층위를 잇는 것이 `open_debts` 원장이다.

```
baseline 에서 pre-existing 실패 발견
   → debt_opened { debt_id, cmd, origin_task }
   → task 판정에는 영향을 주지 않는다 (06 차등 판정표)
   → open_debts[] 에 남는다

이후 어떤 task 의 post 에서 green 이 되면
   → debt_closed { debt_id, closed_by }
```

- **debt는 task를 막지 않는다.** 무관한 기존 실패로 올바른 task가 rejected되면 안 되기 때문이다.
- **debt는 ship을 막는다.** 깨진 상태로 배포되면 안 되기 때문이다.
- `harness status`가 open_debts를 항상 노출한다. 조용히 쌓이지 않는다.

---

## Ship 게이트 — canonical

`harness ship`이 통과를 허용하는 조건은 넷이다. **전부 만족해야 한다.**

```
1. 모든 R-### 가 covered
2. 관련된 모든 task 의 최종 verdict 가 verified
3. 드리프트 없음 (spec_hash 일치)
4. open_debts 가 비어 있음
```

유일한 예외는 **기록되는 사람 waiver**다. `.harness/waivers.yaml`에서 읽고, ship이 인정할 때마다 `debt_waived` 이벤트로 journal에 남긴다 (03).

```yaml
# .harness/waivers.yaml
waivers:
  - debt_id: D-002
    approver: "emdhks09@gmail.com"
    reason: "레거시 e2e 스위트. 별도 티켓 PROJ-412 로 추적 중."
    approved_at: "2026-08-27T18:00:00+09:00"
```

waiver 없이 깨진 상태로 ship되는 경로는 없다.

### ship의 절차

```
1. converge.json 이 이 run 의 **현재** 통합 tip 에 대한 것이고 ok 인지 확인한다.
   아니면 "converge 를 먼저 실행하라" 로 실패한다.
2. open_debts 에서 waiver 로 면제된 것을 빼고 남으면 실패한다.
3. 통과하면 통합 브랜치를 사용자의 현재 브랜치로 머지한다. run 동안 사용자
   브랜치는 움직이지 않았으므로 (05) 보통 fast-forward 다. 충돌하면 중단하고
   실패로 보고한다. safe 프로파일 run 은 통합 브랜치가 없다 — 변경이 이미
   메인 워크트리에 있으므로 머지가 없다.
4. .harness/ship-report.md 를 쓴다.
```

---

## 지식 카드

```yaml
# .harness/knowledge/K-004.yaml
id: K-004
kind: pattern              # pattern | pitfall | convention
rule: null                 # learn 이 제안한 카드의 원천 rule. 사람이 쓴 카드는 null 이다.
scope: "src/api/**"
claim: "이 저장소의 라우트 핸들러는 인증을 미들웨어에 위임하고 직접 검사하지 않는다"
evidence:
  - run: run-20260820-1102
    task: T-003
  - run: run-20260825-0930
    task: T-011
status: candidate          # candidate | promoted | retired
uses: 0
```

### 승격과 폐기

- **승격은 사람만 한다.** 서로 다른 run에서 반복 관측되는 것은 `candidate` 자격을 만들 뿐이며,
  그것만으로 `promoted`가 되지 않는다.
- **폐기도 사람이 한다.** 오래 사용되지 않거나 반증하는 관측이 나오면 하네스가 보고하고,
  `retired`로 바꾸는 것은 사람의 명령이다.
- `uses`는 컨텍스트에 실제로 포함된 횟수다. 쓰이지 않는 지식은 지식이 아니다.

### 절대 규칙

- **지식은 constitution을 덮어쓸 수 없다.**
- **지식은 acceptance criteria가 될 수 없다.** 관측에서 나온 경향을 판정 기준으로 승격시키면 하네스가 자기 편견을 검증하게 된다.
- 지식 카드는 컨텍스트에서 **untrusted**다. 07 참조.

### `harness learn`

완료된 run의 journal을 읽어 후보를 제안하고, 카드의 `uses`를 갱신하며, 폐기 대상을 보고한다.
**상태를 바꾸는 것은 사람의 명령뿐이다.**

| 커맨드 | 하는 일 |
|---|---|
| `harness learn` | 후보 제안 · `uses` 갱신 · 폐기 대상 보고 |
| `harness learn promote K-004` | `status: promoted`로 기록 |
| `harness learn retire K-004` | `status: retired`로 기록 |

**후보의 원천은 반복된 review finding이다.** 서로 다른 run에서 같은 `rule`이
`knowledge.candidate_after`회 이상 관측되면 그 rule로 `candidate` 카드를 만든다.

- **같은 run 안의 반복은 한 번으로 센다.** 한 run의 여러 wave와 여러 리뷰어는 독립 관측이 아니다.
- **`rule`이 카드와 관측을 잇는 키다.** 같은 `rule`의 카드가 이미 있으면 새로 만들지 않고
  `evidence`에 `{run, task}`를 더한다.
  `retired` 카드는 다시 후보가 되지 않는다 — 사람이 내린 판단을 하네스가 뒤집지 않는다.
- `scope`는 그 rule이 지적한 파일들의 공통 디렉토리에 `/**`를 붙인 값이다.
- `claim`은 `[NEEDS CLAIM]`으로 남긴다. **learn은 관측을 모을 뿐 문장을 지어내지 않는다.**
- `kind`는 `pitfall`이다. 반복되는 지적은 패턴이 아니라 함정이다.

**`uses`는 `context.manifest.json`의 L7 섹션 기록에서 센다.** 예산 때문에 탈락한 섹션은 세지
않는다 — 프롬프트에 실제로 들어간 것만 사용이다. 실행 경로에 카운터를 심지 않는다 (11).

폐기 보고 기준은 `knowledge.retire_after_unused_runs`다. `promoted` 카드가 그 수 이상의 run
동안 한 번도 컨텍스트에 포함되지 않았으면 보고한다.

### 08이 소유하는 config 키 — knowledge

```yaml
knowledge:
  candidate_after: 2            # 서로 다른 run 에서 같은 rule 이 이만큼 반복되면 후보
  retire_after_unused_runs: 10  # promoted 카드가 이만큼의 run 동안 안 쓰이면 폐기 보고
```
