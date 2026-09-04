# 12 · 로드맵

*이 문서는 [`docs/12-ROADMAP.md`](../12-ROADMAP.md)의 번역이다. 둘이 어긋나면 영문이 canonical이다.*

각 역량은 **정확히 한 마일스톤에만** 등장한다. 어떤 기능이 두 마일스톤에 걸쳐 있으면 그것은 범위가 잘못 잘린 것이다.

마일스톤은 순서대로 진행한다. 뒤 마일스톤의 기능을 앞당기지 않는다.

---

## M0 — 커널 계약

**만드는 것**

`models`, `events`, `schemas/`, `config`, `store`(journal append + state projection + 재구성), `git` 래퍼, `adapters/{base, registry, mock}`, `cli`의 `init`·`status`·`doctor`.

**완료 기준**

- `state == fold(journal)` 재구성 테스트 통과
- 임의 지점에서 프로세스를 죽인 뒤 journal로 상태가 복원됨
- `harness init` 이 만든 저장소에서 `doctor` 가 아무 지적 없이 통과한다
- **LLM도 네트워크도 없이 동작한다**

---

## M1 — 실행과 판정

**만드는 것**

`dag`, `exec/runner`(sequential, `max_parallel=1`), outbox 규약(claim + handoff), `probes`, **`policy`(Command Policy)**, `exec/verify`(baseline/post AC + `kind`별 diff 기대 + 차등 판정 + debt), **`exec/handoff`**(required/optional 게이트 + `repairing`), blocked/error 분류, exit code 규칙, `adapters/generic_cli`, `adapters/conformance`, `cli`의 `run`, 회귀 eval 러너.

**완료 기준**

`mock`과 `generic_cli` 양쪽에서 3-task DAG를 완주한다. 그리고 다음 다섯 가지를 **테스트로 증명한다.**

1. **claim이 깨져 있어도 `verified`가 가능하다**
2. **required handoff 누락이 구현을 버리지 않는다** (`repairing`으로 handoff만 복구)
3. **`deny` 커맨드가 실행되지 않는다**
4. **무관한 기존 실패가 task를 rejected시키지 않는다**
5. **non-zero exit여도 증거가 통과하면 `verified`다**

이 다섯 개가 v2의 핵심 계약 전부다. M1에서 증명되지 않으면 뒤 마일스톤은 의미가 없다.

---

## M2 — 격리와 재개

**만드는 것**

`worktree` 프로파일(**저장소 밖 배치**), `exec/workspace`, 경로 스코프 강제, 통합 브랜치, `run --resume`, `doctor` 복구.

**완료 기준**

- 경로 위반이 자동으로 `rejected`가 된다
- 강제 종료 후 재개가 journal로 복원되고 **멱등**이다

---

## M3 — 병렬

**만드는 것**

`exec/scheduler` — `max_parallel: N`, ready-set 계산, `allowed_paths` 충돌 직렬화, 머지 큐.

**완료 기준**

- 경로가 겹치지 않는 task는 동시에 실행된다
- 겹치는 task는 직렬화된다
- **journal writer는 여전히 하나다**

---

## M4 — 컨텍스트 선택

**만드는 것**

`context/{builder, repomap, slicing, budget}`, `context.manifest.json`, provenance 기반 신뢰 표기, 프롬프트 구획 규약.

**완료 기준**

프롬프트 토큰이 전량 주입 대비 **측정 가능하게** 감소한다. 감소량은 `context_tokens` 지표로 보고된다.

---

## M5 — 리스크와 리뷰

**만드는 것**

`risk`(effective_risk), 리뷰 티어, bounded wave, fixer, 예산 상한.

**완료 기준**

`trivial`로 선언된 task가 민감 경로를 변경하면 **자동으로 티어가 상향되어 리뷰된다.** `risk_escalated` 이벤트로 확인 가능하다.

---

## M6 — 스펙 파이프라인

**만드는 것**

`spec`/`clarify`/`plan`/`tasks`, `analyze`(Command Policy 사전 검출 포함), `converge`, ship 게이트, debt 원장.

**완료 기준**

다음 셋이 **각각** 게이트를 막는다.

- 미커버 요구사항 → `converge` 실패
- 미해소 debt → `ship` 실패
- `deny` 커맨드가 포함된 task → `analyze` 실패

---

## M7 — 벤더 어댑터와 능력 평가

**만드는 것**

`adapters/{claude_cli, codex_cli}`(usage 보고), 능력 eval — fixtures, arms, hidden grader, 반복 실행과 분산 보고.

**완료 기준**

동일한 hidden grader로 `raw` vs `harness-full` 비교 리포트를 산출한다. `escape_rate`와 `false_block_rate`가 측정된다.

**이 마일스톤이 프레임워크의 존재 이유를 검증한다.** 결과가 개선을 보이지 않으면 설계를 다시 본다.

---

## M8 — 옵션 레이어

**만드는 것**

`learn`, `container` 프로파일, cross-model adversarial 리뷰, 외부 벤치마크 fixture 어댑터.

**완료 기준**

**옵션 레이어를 전부 제거한 상태에서 커널이 동작함을 테스트로 증명한다.** 02의 커널/옵션 경계가 실재함을 이 테스트가 강제한다.

---

## 구현 세션을 위한 리허설 체크리스트

각 마일스톤에 착수하는 세션은 **이 문서 세트만 읽고** 시작할 수 있어야 한다. 다음 질문의 답이 문서 안에 없으면 그것은 설계의 빈틈이지 구현자가 정할 일이 아니다.

**M0 착수 전**
- journal 이벤트의 종류와 페이로드는? → 03
- `seq` 부여 규칙과 이벤트 `id` 형식은? → 03
- `state == fold(journal)` 재구성 절차와 크래시 복구 순서는? → 03, 10
- `mock` 어댑터가 만족해야 할 conformance 항목 전체는? → 04
- `init` 이 만들어야 할 control-plane 항목은? → 03 의 파일 배치

**M1 착수 전**
- AC baseline/post의 cwd·타임아웃·exit code 해석과 green/red 분류 규칙은? → 06
- claim과 handoff가 각각 없거나 invalid할 때 판정이 어떻게 갈리는가? → 06
- Command Policy의 판정 순서, 승인 저장 형식, 무인 실행 시 동작은? → 06
- `blocked` 판정에 필요한 최소 증거는? `error`와의 경계는? → 06, 10
- `rejected → ready`, `repairing → verified` 전이 조건과 시도 소진 판정은? → 03, 06
- 회귀 eval fixture 하나를 처음부터 작성하는 절차는? → 11

**M2 이후**
- 워크트리·outbox의 정확한 경로와 생명주기는? → 05
- 경로 스코프 glob 매칭 규칙은? → 05
- 재개 시 state별 처리는? → 10

---

## 문서 유지 규칙

- 한 개념의 canonical 정의는 **정확히 한 문서에만** 있다. 다른 문서는 참조하며 재서술하지 않는다. canonical 위치는 00의 문서 지도에 있다.
- 계약이 바뀌면 canonical 문서를 고치고, 그것으로 대체된 문장은 **삭제한다.** 옛 서술과 새 서술을 나란히 두지 않는다.
- **개정 이력을 문서 본문에 남기지 않는다.** 문서는 최종 계약만 기술한다. 이력은 git이 갖는다.
