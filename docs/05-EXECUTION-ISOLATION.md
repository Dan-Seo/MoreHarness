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

## `container`

OS 수준 격리를 제공하는 유일한 프로파일이다. 하네스는 **agent 프로세스만** 컨테이너 안에서
실행한다. AC·precondition·health 커맨드와 diff 관측은 호스트에서 그대로 돈다 — 판정은
하네스 소유 증거이고, 그 증거를 agent 와 같은 봉투 안에서 만들지 않는다.

워크스페이스 생명주기는 `worktree` 와 같다. 컨테이너는 **실행 방식이지 워크스페이스 배치가
아니다.**

```yaml
container:                     # 05 가 소유하는 config 키
  runtime: docker              # 실행 파일 이름. docker | podman
  image: "example/agent:1"     # 필수
  network: null                # null 이면 런타임 기본값. "none" 이면 네트워크를 끊는다
  mounts: []                   # 추가 마운트. "<host>:<container>[:ro]" 문자열 목록
```

어댑터가 만든 argv 를 다음 형태로 감싼다.

```
<runtime> run --rm
  -v <workspace>:<workspace>  -v <outbox>:<outbox>
  -w <workspace>
  [-i]                         # prompt_delivery 가 stdin 이면
  -e <NAME> ...                # 값이 아니라 이름만
  [--network <network>]
  [-v <mount> ...]
  <image>
  <어댑터가 만든 argv...>
```

- **경로는 호스트와 컨테이너에서 같다.** 워크스페이스와 outbox 를 같은 경로로 마운트하므로
  프롬프트와 매니페스트에 적힌 경로가 컨테이너 안에서도 그대로 유효하다.
- **`.harness/` 를 마운트하지 않는다.** control-plane 은 컨테이너 안에서 보이지 않는다.
- 환경변수는 `-e NAME` 으로 **이름만** 넘긴다. 값은 런타임 CLI 프로세스의 환경에서 전달되므로
  프로세스 목록에 비밀이 남지 않는다. 어떤 이름이 넘어가는지는 04 의 `env_passthrough` 규약
  그대로다.
- **network 기본은 런타임 기본값이다.** agent CLI 는 모델 API 에 접근해야 하므로 하네스가
  임의로 끊지 않는다. 끊으려면 `network: none` 을 명시한다.
- 컨테이너가 만든 파일의 소유자는 런타임이 정한다. 하네스는 보정하지 않는다.

`runtime` 실행 파일이 없으면 준비물 부족이므로 verdict `blocked` 다. `image` 미선언은 설정
결함이므로 `error` 다. 분류표는 10 이 canonical 이다.

**`container` 에서 준비물 검사의 대상은 어댑터의 실행 파일이 아니라 `runtime` 이다.** agent
CLI 는 이미지 안에 있고 호스트에 없을 수 있으므로, 어댑터 preflight 의 `missing_prerequisite`
는 이 프로파일에서 `blocked` 로 이어지지 않는다. 설정 결함(`misconfigured`)은 그대로 `error`
다 (04 의 preflight 분류).

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
1. 준비    <system temp>/harness/<repo-key>/<run-id>/<task-id>/worktree 에 git worktree 생성
           브랜치: harness/<run-id>/<task-id>
2. outbox  ../outbox/attempt-<n>/ 생성. 워크트리 밖이다.
3. 실행    어댑터가 cwd = worktree 로 프로세스 실행
4. 관측    git diff 로 changed_files / created_files / diff_stat 계산
5. 판정    경로 스코프 + AC post (06)
6. 통합    증거와 handoff 게이트를 통과했으면 머지한다. 판정 순서는 06 이 canonical 이다.
7. 정리    성공 시 워크트리 제거. 실패 시 보존하고 경로를 기록한다.
```

실패한 워크트리를 남기는 것은 사람이 조사할 수 있게 하기 위해서다. 고아 워크트리는 `harness doctor`가 정리한다.

**하네스는 `constitution.md`와 `config.yaml`을 항상 메인 저장소에서 읽는다.** 워크트리 안의 사본은 agent가 수정할 수 있는 저장소 콘텐츠이므로 control-plane 입력으로 쓰지 않는다.

---

## 통합 브랜치

task마다 브랜치를 만들면 결과가 흩어진다. 모이는 곳이 없으면 downstream task가 upstream의 변경을 보지 못하고 `depends_on` 선언이 무의미해진다.

```
run 시작    HEAD 에서 harness/<run-id>/integration 을 만든다
task 준비   통합 브랜치의 현재 tip 에서 harness/<run-id>/<task-id> 를 만든다
task 종료   워크트리의 변경을 task 브랜치에 커밋한다
통합        task 브랜치를 통합 브랜치로 머지한다
```

- **task 워크트리는 run 시작 시점의 HEAD가 아니라 통합 브랜치의 현재 tip에서 분기한다.** 앞선 task가 만든 것이 뒤 task의 워크스페이스에 실제로 있어야 하기 때문이다.
- **사용자의 브랜치는 run 동안 한 번도 바뀌지 않는다.** run 결과를 버리려면 `harness/<run-id>/*`를 지우면 되고, 사용자 브랜치로 옮기는 것은 `ship`의 일이다. 08 참조.
- **커밋은 하네스가 한다.** agent가 커밋했는지 여부는 판정에 영향을 주지 않는다. 판정의 입력은 커밋이 아니라 diff이며, 커밋은 머지를 가능하게 하는 수단일 뿐이다.
- 머지 충돌은 verdict 없이 state `integration_conflict`다. 10 참조.
- **`safe` 프로파일에는 통합이 없다.** 워크트리가 없으므로 변경이 이미 메인 워크트리에 있다.

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

### glob 방언

gitignore 계열이다.

| 패턴 | 매칭 |
|---|---|
| `*` | `/`를 넘지 않는 0글자 이상 |
| `?` | `/`가 아닌 한 글자 |
| `**` | 0개 이상의 경로 세그먼트. `src/api/**`는 `src/api` 자신과 그 아래 전부를 덮는다 |
| 그 밖 | 리터럴 |

- 패턴은 항상 저장소 루트 기준이다. `*.py`는 루트의 `.py` 파일만 가리키고 하위 디렉토리는 가리키지 않는다.
- **`allowed_paths`를 선언하지 않으면 허용 범위에 제한이 없다.** 이때도 `effective_forbidden`은 그대로 적용된다. task마다 경로를 선언하게 만드는 것은 `analyze`의 일이지 판정의 일이 아니다. 08 참조.
- 매칭은 경로 문자열만 본다. 그 파일이 지금 실재하는지는 보지 않는다 — 삭제된 경로도 판정 대상이다.
- **TDD 모드(06)는 단계마다 다른 `allowed`·`effective_forbidden` 을 이 판정에 넣는다.** 판정 절차 자체는 여기 그대로이며, 단계별 조합은 06 이 canonical 이다.

---

## 병렬 실행과 통합 큐

병렬 스케줄러는 옵션이다 (02). `max_parallel`이 1이면 커널의 sequential runner만으로 동작하고, 2 이상이면 스케줄러가 실행을 맡는다. `max_parallel`은 **동시에 실행 중인 task 수의 상한**이다.

### 스케줄링

```
ready_set = { t | t.depends_on 이 전부 verified }
동시 실행 대상 선정:
  ready_set 을 위상 정렬 순서(같은 층은 task id 순)로 훑으며,
  실행 중이거나 이미 선택된 task 와 겹치면 건너뛴다 (직렬화)
  실행 중 + 선택 수가 max_parallel 에 도달하면 중단
```

경로가 겹치는 task를 동시에 돌리면 diff 귀속이 모호해지고 판정이 무의미해진다. **겹치면 직렬**이 유일한 규칙이다.

### 겹침 판정

판정은 보수적이다. **과잉 직렬화는 병렬 기회를 잃을 뿐이지만, 과소 판정은 판정을 오염시킨다.** 다음 순서로 본다.

1. `safe` 프로파일 task는 메인 워크트리를 공유하므로 **모든 task와 겹친다.**
2. `allowed_paths`를 선언하지 않은 task는 허용 범위에 제한이 없으므로 **모든 task와 겹친다.**
3. 선언된 두 집합은 패턴 쌍 단위로 본다 — 각 패턴에서 첫 와일드카드(`*`·`?`) 앞까지의 리터럴 접두사를 취해, **한쪽이 다른 쪽의 접두사이면 겹친다.** `src/api/**`와 `src/web/**`는 분리되고, `src/**`와 `src/api/**`는 겹친다.

건너뛴 task는 버려지지 않는다. 겹치던 task가 끝난 뒤의 선정에서 다시 고려되며, 그때의 통합 브랜치 tip에서 분기하므로 앞선 task의 결과를 본다.

`analyze`는 병렬 가능성이 선언된 task 사이의 `allowed_paths` 충돌을 사전에 보고한다. 08 참조.

### 통합

- **통합은 항상 직렬이다.** 병렬화 대상이 아니다.
- 증거 조건과 handoff 게이트를 통과한 task가 머지 큐에 들어간다. **머지가 성공해야 verdict `verified`가 기록된다.**
- 머지 충돌 시 verdict 없이 state `integration_conflict`로 가고, replan 또는 사람에게 넘어간다.
- journal writer는 여전히 오케스트레이터 프로세스 하나뿐이다. 03 참조.

---

## 05가 소유하는 config 키

```yaml
forbidden_paths: [".harness/**"]   # 전역 금지 목록. task 의 forbidden_paths 와 합집합이다.
container: null                    # container 프로파일의 실행 계약. 위 「container」 절.
```

**`.harness/**`는 이 목록에서 뺄 수 없다.** 설정에서 지우더라도 하네스가 다시 넣는다. control-plane을 agent가 고칠 수 있게 만드는 설정은 존재하지 않아야 한다.
