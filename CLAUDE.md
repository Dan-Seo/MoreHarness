# Harness Framework v2

> **Agents propose. Harness verifies. Evidence decides.**

코딩 agent를 실행하고, **그 결과가 진짜인지 하네스가 직접 확인하는** 프레임워크.

## 현재 상태

설계 문서 13개가 확정되어 있고 **M3(병렬)까지 구현되어 있다. 다음은 M4(컨텍스트 선택)이다.**
마일스톤에 착수하기 전 `docs/12-ROADMAP.md`의 "구현 세션을 위한 리허설 체크리스트"를 확인할 것. 거기 질문의 답이 문서에 없다면 그것은 설계의 빈틈이지 구현자가 임의로 정할 일이 아니다.

## 기술 스택

- Python 3.11+
- **런타임 의존성은 stdlib + `PyYAML` + `jsonschema` 뿐이다.** 추가하려면 먼저 정당화할 것.
- 테스트는 pytest
- 벤더 SDK를 커널에 넣지 않는다. agent CLI는 서브프로세스로만 호출한다.

## 설계 문서가 계약이다

한 개념의 canonical 정의는 **정확히 한 문서에만** 있다.

| 개념 | canonical |
|---|---|
| verdict · state · 이벤트 목록 · 파일 배치 | `docs/03-DATA-MODEL.md` |
| 어댑터 계약 · conformance | `docs/04-AGENT-ADAPTER.md` |
| 프로파일 보장/미보장 | `docs/05-EXECUTION-ISOLATION.md` |
| 판정 알고리즘 · Command Policy | `docs/06-VERIFICATION-REVIEW.md` |
| context provenance · 신뢰 | `docs/07-CONTEXT-SELECTION.md` |
| ship 게이트 · debt 원장 | `docs/08-CONVERGENCE-LEARN.md` |
| 위협 모델 · 비보장 목록 | `docs/09-SECURITY.md` |
| 실패 분류표 | `docs/10-FAILURE-RECOVERY.md` |
| 지표 정의 | `docs/11-EVALUATION.md` |
| 의존 방향 · 커널/옵션 경계 | `docs/02-ARCHITECTURE.md` |

- CRITICAL: 계약을 바꿔야 하면 **먼저 canonical 문서를 고치고, 그것으로 대체된 문장을 삭제**한 뒤 코드를 쓸 것. 코드가 문서와 다르면 코드가 틀린 것이다.
- 같은 규칙을 두 문서에 재서술하지 말 것. 참조만 한다.
- 개정 이력("이전에는 ~였으나")을 문서 본문에 남기지 말 것. 이력은 git이 갖는다.

## 아키텍처 규칙

- CRITICAL: 판정은 **하네스 소유 증거로만** 한다. agent가 쓴 claim, handoff, exit code를 판정 근거로 삼는 코드를 작성하지 말 것. claim은 optional 힌트이며 없거나 깨져도 `verified`가 가능해야 한다.
- CRITICAL: 하네스가 서브프로세스를 띄우는 **모든 지점**이 `policy.py`를 통과한다. AC, precondition, health 커맨드, verifier, eval fixture — 예외 없음. 기본은 argv 리스트 + `shell=False`.
- CRITICAL: **순환 의존 금지.** `models ← events/store/dag/risk/probes/policy ← exec/context ← eval ← cli`. `eval`은 `cli`를 import하지 않는다.
- CRITICAL: 쓰기 순서는 **journal append + fsync → state 갱신**이다. 뒤집지 말 것. journal 이벤트는 불변이며 정정은 삭제가 아니라 새 이벤트로 한다.
- verdict는 5개(`verified`/`rejected`/`blocked`/`error`/`budget_exhausted`)로 고정한다. `repairing`, `needs_replan`, `integration_conflict`, `human_required`는 verdict가 아니라 state다. 새 verdict를 만들지 말 것.
- 워크트리와 outbox는 **저장소 밖**이다. `.harness/`는 하네스 전용이며 agent 영역이 아니다. constitution과 config는 항상 메인 저장소에서 읽는다.
- 어댑터는 판정하지 않는다. 파싱·검증·판정은 전부 하네스가 한다. 어댑터는 프로세스를 띄우고 결과의 **경로**만 돌려준다.
- 지표는 journal의 projection이다. **지표용 카운터를 코드에 심지 말 것.** 새 지표가 필요하면 먼저 필요한 이벤트가 있는지 본다.
- `sys.exit`는 `cli.py`에만 존재한다. 다른 모듈은 예외를 올리거나 값을 반환한다.
- 보안 표현 규약: "agent는 쓸 수 없다"가 아니라 **"하네스는 승인하지 않으며 위반을 탐지한다"**. OS 수준 봉쇄는 `container` 프로파일뿐이며, Command Policy도 worktree도 보안 경계가 아니다.

## 개발 프로세스

- CRITICAL: TDD. **계약을 검증하는 테스트를 먼저** 쓰고, 통과하는 구현을 쓴다. 테스트는 구현 세부가 아니라 문서의 계약을 검증해야 한다.
- 마일스톤 순서(M0~M8)를 지킨다. 뒤 마일스톤의 기능을 앞당기지 않는다. 각 역량은 정확히 한 마일스톤에만 등장한다.
- 커널에 기능을 추가하기 전에 **어느 문서의 어느 계약을 바꾸는지** 먼저 밝힌다. 계약을 바꾸지 않는 기능은 커널에 들어갈 이유가 없다.
- 커널 약 1,500줄은 **관찰 지표이지 합격 조건이 아니다.** 줄 수를 맞추려고 가독성을 희생하지 말 것.
- 커밋 메시지는 conventional commits (`feat:`, `fix:`, `docs:`, `refactor:`, `chore:`, `test:`).

## 명령어

```
pytest                    # 테스트
python -m harness init    # 저장소에 .harness/ 생성 (멱등)
python -m harness run     # tasks/ 의 DAG 실행 (safe · worktree 프로파일)
python -m harness run --resume <run-id>   # 죽은 run 을 이어서 실행
python -m harness status  # 현재 run 상태, open_debts, human_required
python -m harness doctor  # 일관성 검사 및 복구
```

나머지 커맨드(`spec`·`plan`·`tasks`·`analyze`·`converge`·`ship`·`learn`·`eval`)는 뒤 마일스톤에서 만든다.
