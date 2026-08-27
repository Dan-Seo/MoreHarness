# 01 · 용어

이 문서가 용어의 canonical 정의다. 다른 문서에서 같은 개념을 다른 이름으로 부르지 않는다.

## 가장 중요한 구분

네 가지가 자주 뭉뚱그려지고, 뭉뚱그려지는 순간 하네스는 agent의 자기 보고를 판정으로 쓰게 된다.

```
Claim      agent가 "내가 무엇을 했다"고 말한 것        — 서사. 힌트. optional.
Handoff    agent가 다음 task를 위해 넘기는 구조화 데이터 — 계약. 별개 파일.
Evidence   하네스가 직접 관측한 것                     — 사실. 판정의 유일한 근거.
Verdict    하네스가 evidence로 내린 결론               — 5개 값 중 하나.
```

**Claim은 Evidence가 아니다.** claim이 없어도, 깨져 있어도, 거짓이어도 판정은 동일하게 진행된다.

---

## 파이프라인 산출물

**Intent** — 사람이 자연어로 말한 목적. 파이프라인의 입력.

**Spec** — Intent를 검증 가능한 요구사항 집합으로 정리한 것. `specs/<slug>/spec.yaml`. 미해소 질문은 `[NEEDS CLARIFICATION]` 마커로 남으며 `analyze`가 이를 막는다.

**Requirement (`R-###`)** — 스펙의 원자 단위. **수렴 추적의 축**이다. 모든 task는 자기가 어떤 R-###를 만족시키는지 `satisfies`로 선언하고, `converge`는 R-### → task → verdict → evidence 매트릭스를 만든다.

**Plan** — 요구사항을 어떤 순서와 구조로 구현할지에 대한 결정. Task DAG의 근거.

**Task** — 실행 단위. `tasks/T-###.task.yaml`. 계약은 03에 정의된다.

**Task DAG** — task 간 `depends_on` 관계가 만드는 유향 비순환 그래프. 병렬 가능성의 근거이자 `analyze`의 검사 대상.

---

## 실행 단위

**Run** — 한 번의 `harness run` 실행. `run_id`로 식별되고 `.harness/runs/<run-id>/` 아래 모든 기록을 갖는다.

**Attempt** — 한 task에 대한 한 번의 agent 실행 시도. `rejected`나 `error` 후 재시도하면 attempt 번호가 올라간다. **verdict는 attempt 단위로 부여된다.**

**Workspace** — agent의 cwd. `worktree` 프로파일에서는 저장소 밖에 만들어진 git worktree다. 05 참조.

**Outbox** — agent가 결과 아티팩트(claim, handoff)를 쓰는 디렉토리. 워크스페이스 밖이고 `.harness/` 밖이다. attempt마다 새로 만들어진다. 04 참조.

---

## 판정에 관한 것

**Claim** — agent의 자기 보고. **optional이며 힌트다.** 없어도 틀려도 판정은 진행된다. 어떤 verdict도 claim만으로 결정되지 않는다. claim의 `blocked_hint`는 결론이 아니라 하네스가 확인해 볼 가설이며, 하네스가 probe로 확인하지 못하면 인정되지 않는다.

**Handoff** — 다음 task가 쓸 구조화 데이터. claim과 **별개 아티팩트**다. 서사와 데이터는 소비자도 수명도 다르기 때문에 파일을 분리한다. 하나가 깨져도 다른 하나는 살아남는다.

**Evidence** — 하네스가 직접 만든 관측치. precondition probe 결과, AC 실행 결과, git diff, 경로 판정, Command Policy 판정, 리뷰 finding. **판정의 유일한 근거.**

**Acceptance Criteria (AC)** — task가 만족해야 할, 하네스가 직접 실행하는 커맨드. agent가 실행하는 것이 아니다. `expect_fail_before`가 붙으면 red→green 증명을 요구한다. 06 참조.

**Verdict** — 판정 결과. **`verified | rejected | blocked | error | budget_exhausted`** 다섯 개뿐이다. canonical 정의는 03.

**State** — task의 워크플로 위치. verdict와 **다른 축**이다. `repairing`, `needs_replan`, `human_required` 같은 값은 state이지 verdict가 아니다. canonical 정의는 03.

**Finding** — 리뷰어가 낸 구조화된 지적. `{severity, rule, file, line, message, blocking}`. `blocking`은 결정론적 규칙으로 계산된다.

**Debt** — 어떤 task의 책임도 아니지만 해소되지 않은 문제. 대표적으로 task 실행 전부터 실패하고 있던 AC. **task 판정은 막지 않고 ship을 막는다.** 08의 `open_debts` 원장이 관리한다.

---

## 기록

**Event** — journal에 append되는 불변 레코드. **canonical 진실이자 모든 지표의 원천.** 정정은 삭제가 아니라 새 이벤트로 한다.

**Journal** — `journal.jsonl`. run의 전체 이벤트 로그. 단일 writer.

**State projection** — `state.json`. journal을 fold해서 만든 파생 스냅샷. 언제든 재구성 가능하다. journal이 canonical이고 state는 캐시다.

---

## 정책과 격리

**Execution profile** — `safe | worktree | container | unsafe`. 각 프로파일이 무엇을 보장하고 무엇을 보장하지 않는지는 05가 canonical이다.

**Command Policy** — 하네스가 커맨드를 실행하기 전에 통과시켜야 하는 승인 절차. `allow | deny | require_approval`, 기본값 fail-closed. `allow`는 "이 명령이 본질적으로 안전하다"가 아니라 **"이 프로젝트에서 자동 실행이 승인된 command class"** 라는 뜻이다. canonical 정의는 06, 보안적 의미는 09.

**effective_risk** — `max(declared_risk, path_floor, diff_floor)`. task가 선언한 risk를 그대로 믿지 않고, 변경 경로와 diff 규모로 바닥값을 올린다. 리뷰 티어를 결정한다. 06 참조.

**Provenance / trust** — 컨텍스트 각 구획의 출처와 그에 따른 신뢰 등급. 신뢰는 계층 번호가 아니라 **출처로만** 결정된다. canonical 정의는 07.

---

## 학습과 측정

**Knowledge card** — 반복 관찰에서 승격된 재사용 가능한 지식. `.harness/knowledge/K-###.yaml`. **constitution을 덮어쓸 수 없고, acceptance criteria가 될 수 없다.**

**Constitution** — `.harness/constitution.md`. 프로젝트가 절대 어기지 않는 규칙. 하네스 control-plane에 있으며 항상 trusted다.

**Fixture** — eval의 단위 사례. 초기 저장소, spec, hidden grader를 갖는다. 11 참조.

**Arm** — eval에서 비교하는 실행 조건. `raw`, `harness-lite`, `harness-full`, `ablation:<feature>`.

**Hidden grader** — fixture에 숨겨진 채점 기준. **task의 acceptance에도, agent 컨텍스트에도 절대 들어가지 않는다.** 모든 arm을 동일한 기준으로 채점하는 외부 oracle이다.
