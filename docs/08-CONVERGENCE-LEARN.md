# 08 · 수렴과 학습

이 문서는 **ship 게이트와 debt 원장의 canonical 정의**를 갖는다.

두 개의 게이트가 있다. 하나는 구현 전에, 하나는 구현 후에 선다.

```
tasks  →  analyze  →  run  →  converge  →  ship
          (사전)              (사후)
```

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

### 산출

`analyze-report.md`. 실패 항목이 하나라도 있으면 `harness run`을 막는다.

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
- **고아 diff** — 어떤 task의 `allowed_paths`로도 설명되지 않는 변경이 통합 브랜치에 있다.
- **open_debts** — 아래 원장이 비어 있는가.
- **전체 AC 합집합 실행** — 통합 브랜치에서 모든 task의 AC와 프로젝트 health 커맨드를 실행한다. Command Policy를 통과한다.

### 산출

`coverage.md`.

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

유일한 예외는 **기록되는 사람 waiver**다.

```yaml
# waiver 는 journal 에 이벤트로 남고 ship 리포트에 표시된다
waivers:
  - debt_id: D-002
    approver: "emdhks09@gmail.com"
    reason: "레거시 e2e 스위트. 별도 티켓 PROJ-412 로 추적 중."
    approved_at: "2026-08-27T18:00:00+09:00"
```

waiver 없이 깨진 상태로 ship되는 경로는 없다.

---

## 지식 카드

```yaml
# .harness/knowledge/K-004.yaml
id: K-004
kind: pattern              # pattern | pitfall | convention
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

- **승격** — 사람이 승인하거나, 서로 다른 run에서 N회 독립 재현되면 `promoted`.
- **폐기** — 오래 사용되지 않거나, 반증하는 관측이 나오면 `retired`.
- `uses`는 컨텍스트에 실제로 포함된 횟수다. 쓰이지 않는 지식은 지식이 아니다.

### 절대 규칙

- **지식은 constitution을 덮어쓸 수 없다.**
- **지식은 acceptance criteria가 될 수 없다.** 관측에서 나온 경향을 판정 기준으로 승격시키면 하네스가 자기 편견을 검증하게 된다.
- 지식 카드는 컨텍스트에서 **untrusted**다. 07 참조.

`harness learn`은 완료된 run의 journal을 읽어 후보를 제안하고, 승격·폐기를 기록한다. 자동 승격은 하지 않는다.
