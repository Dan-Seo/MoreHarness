# 05 · 실행과 격리

이 문서는 **실행 프로파일이 무엇을 보장하고 무엇을 보장하지 않는지에 대한 canonical 정의**를 갖는다.

## 프로파일

| 프로파일 | 실행 위치 |
|---|---|
| `safe` | 메인 워크트리. 변경을 기대하지 않는 `readonly`/`analysis` task 용 |
| `worktree` | 저장소 밖에 만든 git worktree. **implementation 의 기본값** |
| `container` | 컨테이너 안. 유일하게 OS 수준 격리를 제공한다 |
| `unsafe` | 제약 없이 실행. 절대 기본값이 아니다 |

---

## 보장하는 것과 보장하지 않는 것 — canonical

|  | `safe` | `worktree` | `container` |
|---|---|---|---|
| cwd 고정 | 있음 | 있음 | 있음 |
| branch/worktree 분리 | 없음 | 있음 | 있음 |
| git diff 범위 검증 | 있음 | 있음 | 있음 |
| 허용 경로 사후 검사 | 있음 | 있음 | 있음 |
| 실수·오염 탐지 | 있음 | 있음 | 있음 |
| **OS filesystem 격리** | **없음** | **없음** | 있음 |
| **credential 격리** | **없음** | **없음** | 있음 |
| **network 격리** | **없음** | **없음** | 설정 가능 |
| **악의적 agent containment** | **없음** | **없음** | 부분적 |

### 표현 규약

이 문서와 다른 모든 문서에서 다음 표현을 쓴다.

> **"agent는 쓸 수 없다"가 아니라, "하네스는 쓰기를 승인하지 않으며 저장소 스코프 위반을 탐지한다. OS 수준 봉쇄는 `container` 프로파일이 필요하다."**

같은 OS 사용자 권한으로 실행되는 일반 CLI agent는 다른 경로, `$HOME`, credential 파일, 네트워크에 접근할 수 있다. `worktree`는 이것을 막지 않는다. 막는 척하는 문서는 사용자를 잘못된 안전감에 빠뜨리므로 금지한다.

### `.harness/**`에 대해 실제로 하는 세 가지

1. agent 프롬프트와 도구 정책에서 접근을 금지한다.
2. 워크트리를 저장소 밖에 두어 **기본 접근 경로에서 제외**한다.
3. diff가 `.harness/`에 닿으면 `path_violation`으로 **탐지**한다.

`worktree` 프로파일만으로 OS 레벨의 절대 쓰기 금지를 주장하지 않는다.

---

## `unsafe`

- **절대 기본값이 아니다.**
- config의 명시적 허용 **그리고** CLI의 명시적 플래그가 **둘 다** 있어야 활성화된다.
- 활성화 시 배너를 출력한다.
- run manifest에 기록된다.
- 리뷰 티어가 자동 상향된다.

---

## 워크스페이스 생명주기

```
1. 준비    <system temp>/harness/<run-id>/<task-id>/worktree 에 git worktree 생성
           브랜치: harness/<run-id>/<task-id>
2. outbox  ../outbox/attempt-<n>/ 생성. 워크트리 밖이다.
3. 실행    어댑터가 cwd = worktree 로 프로세스 실행
4. 관측    git diff 로 changed_files / created_files / diff_stat 계산
5. 판정    경로 스코프 + AC post (06)
6. 통합    verified 인 경우에만 머지 큐로
7. 정리    성공 시 워크트리 제거. 실패 시 보존하고 경로를 기록한다.
```

실패한 워크트리를 남기는 것은 사람이 조사할 수 있게 하기 위해서다. 고아 워크트리는 `harness doctor`가 정리한다.

**하네스는 `constitution.md`와 `config.yaml`을 항상 메인 저장소에서 읽는다.** 워크트리 안의 사본은 agent가 수정할 수 있는 저장소 콘텐츠이므로 control-plane 입력으로 쓰지 않는다.

---

## 경로 스코프 판정

```
effective_forbidden = task.forbidden_paths ∪ config.forbidden_paths
                      (config.forbidden_paths 는 항상 .harness/** 를 포함한다)
```

판정은 **사후**다. agent 실행이 끝난 뒤 `git diff --name-status`로 실제 변경 목록을 얻고,

- `allowed_paths` 밖의 경로가 있으면 → `path_violation` 이벤트 → verdict `rejected`
- `effective_forbidden` 안의 경로가 있으면 → `path_violation` 이벤트 → verdict `rejected`

glob 매칭은 저장소 루트 기준 POSIX 경로로 정규화한 뒤 수행한다. 심볼릭 링크는 따라가지 않고 링크 자체의 경로로 판정한다.

---

## 병렬 실행과 통합 큐

병렬 스케줄러는 옵션이며 M3에서 도입한다. 그전까지 `max_parallel`은 1이다.

### 스케줄링

```
ready_set = { t | t.depends_on 이 전부 verified }
동시 실행 대상 선정:
  ready_set 을 우선순위 순으로 훑으며,
  이미 선택된 task 와 allowed_paths 가 겹치면 건너뛴다 (직렬화)
  선택 수가 max_parallel 에 도달하면 중단
```

경로가 겹치는 task를 동시에 돌리면 diff 귀속이 모호해지고 판정이 무의미해진다. **겹치면 직렬**이 유일한 규칙이다.

`analyze`는 병렬 가능성이 선언된 task 사이의 `allowed_paths` 충돌을 사전에 보고한다. 08 참조.

### 통합

- **통합은 항상 직렬이다.** 병렬화 대상이 아니다.
- verdict `verified`인 task만 머지 큐를 통과한다.
- 머지 충돌 시 verdict 없이 state `integration_conflict`로 가고, replan 또는 사람에게 넘어간다.
- journal writer는 여전히 오케스트레이터 프로세스 하나뿐이다. 03 참조.
