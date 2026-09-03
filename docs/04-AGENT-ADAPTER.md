# 04 · Agent 어댑터

이 문서는 **어댑터 계약의 canonical 정의**를 갖는다.

## 어댑터가 하는 일과 하지 않는 일

어댑터는 **프로세스를 띄우고 결과의 위치를 알려주는 것**까지만 한다.

| 어댑터가 하는 것 | 어댑터가 하지 않는 것 |
|---|---|
| cwd 를 `workspace` 로 고정해 프로세스 실행 | claim/handoff 파싱·검증 |
| `outbox` 경로를 agent 에게 전달 | 성공·실패 판정 |
| timeout 강제, 프로세스 정리 | AC 실행 |
| exit code·stdout·stderr 수집 | diff 해석 |
| 아티팩트 **경로**를 반환 | 아티팩트 **내용**을 해석 |
| usage 를 보고할 수 있으면 보고 | 재시도 결정 |

**파싱과 판정은 전부 하네스가 한다.** 어댑터가 결과를 해석하기 시작하면 벤더마다 판정이 달라지고, 원칙 1이 무너진다.

---

## 프로토콜

```python
@dataclass(frozen=True)
class AgentRequest:
    task_id: str
    prompt: str
    workspace: Path              # agent 의 cwd — 저장소 밖 워크트리
    outbox: Path                 # 워크스페이스 밖. result.json / handoff.json 을 여기에.
    profile: ExecutionProfile
    allowed_tools: list[str] | None
    timeout_s: int
    env: Mapping[str, str]       # 화이트리스트. HARNESS_OUTBOX 를 포함한다.
    attempt: int
    container: ContainerSpec | None   # 05 의 container 프로파일. None 이면 호스트에서 실행한다.


@dataclass(frozen=True)
class AgentResult:
    exit_code: int
    stdout: str
    stderr: str
    raw_claim_path: Path | None       # 파싱·검증은 하네스가 한다
    raw_handoff_path: Path | None
    duration_s: float
    usage: Usage | None               # 벤더가 보고하지 않으면 None
    transcript_path: Path | None
    runtime_failure: RuntimeFailure | None   # timeout | killed | protocol_violation


@dataclass(frozen=True)
class Usage:
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None


@dataclass(frozen=True)
class Capabilities:
    reports_usage: bool
    supports_tool_allowlist: bool
    supports_session_reuse: bool


@dataclass(frozen=True)
class PreflightReport:
    ok: bool
    kind: Literal["ok", "missing_prerequisite", "misconfigured", "internal_error"]
    detail: str


class AgentAdapter(Protocol):
    name: str
    def capabilities(self) -> Capabilities: ...
    def preflight(self) -> PreflightReport: ...
    def execute(self, request: AgentRequest) -> AgentResult: ...
```

`AgentResult`에 `success` 같은 필드가 없는 것은 의도다. 어댑터는 성공을 판단하지 않는다.

---

## 확장 경로

```
mock  →  generic_cli  →  claude_cli / codex_cli
```

**`mock`** — 결정론적. 시나리오로 exit code, 산출 파일, 지연을 지정한다. 시나리오는 어댑터 옵션에 직접 쓰거나 `scenario` 로 파일 경로를 준다. 회귀 eval과 CI의 기본값이며 LLM도 네트워크도 필요 없다.

**`generic_cli`** — 설정만으로 임의 CLI를 구동한다.

```yaml
adapters:
  my_cli:
    type: generic_cli
    command: ["mytool", "run", "--cwd", "{workspace}", "--prompt-file", "{prompt_file}"]
    prompt_delivery: file        # argv | stdin | file
    env_passthrough: [PATH, HOME, LANG]
    usage_from: none             # none | stdout_json:<jsonpath> | file:<path>
    timeout_grace_s: 10
```

placeholder: `{workspace} {outbox} {prompt_file} {timeout_s} {task_id} {attempt}`.

**벤더 어댑터** — `claude_cli`, `codex_cli`. `generic_cli` 위의 얇은 구성이며 **선택적 능력만** 추가한다.

### `claude_cli`

```yaml
adapters:
  claude:
    type: claude_cli
    binary: claude               # 기본값. argv 접두 리스트도 허용한다 (래퍼·테스트용)
    extra_args: []               # argv 뒤에 그대로 붙는다
    env_passthrough: [PATH, HOME]
    timeout_grace_s: 10
```

- 구동: `<binary> -p --output-format json <extra_args...> --add-dir <outbox>` — 프롬프트는 **stdin**으로 준다. outbox 는 워크스페이스 밖이므로(Outbox 규약) `--add-dir` 없이는 claude 가 거기에 쓰지 못한다 — `--permission-mode acceptEdits` 여도 cwd 밖 쓰기는 거부된다.
- **usage 보고** — stdout 전체를 JSON 으로 파싱해 `usage.input_tokens` / `usage.output_tokens` / `total_cost_usd` 를 읽는다. 파싱에 실패하면 usage 는 `None` 이다. 추정하지 않는다.
- **도구 화이트리스트** — `request.allowed_tools` 가 있으면 `--allowedTools <쉼표 연결>` 을 argv 에 추가한다. 현재 커널의 어떤 경로도 이 필드를 채우지 않는다 — 공급원(task 계약 또는 config)의 정의는 이 문서의 계약이 아니며, 정해지기 전까지 값은 항상 `None` 이다.
- 세션 재사용은 지원하지 않는다 — task 단위 fresh context 가 원칙이다 (00 의 원칙 2).

### `codex_cli`

```yaml
adapters:
  codex:
    type: codex_cli
    binary: codex                # 기본값. argv 접두 리스트도 허용한다
    extra_args: []
```

- 구동: `<binary> exec --json <extra_args...> -` — 프롬프트는 **stdin**으로 준다.
- **usage 보고** — stdout 의 각 라인을 JSON 으로 시도 파싱해, `usage` 객체(`input_tokens`/`output_tokens`)를 담은 **마지막** 라인에서 읽는다. cost 는 벤더가 보고하지 않으므로 `None` 이다.
- 도구 화이트리스트·세션 재사용은 지원하지 않는다.

두 어댑터 모두 preflight 에서 binary 미발견은 `missing_prerequisite` 다. 그 외의 계약 — cwd, outbox, timeout, env 화이트리스트, 아티팩트 경로 — 은 `generic_cli` 와 문자 그대로 같으며 conformance 스위트로 증명한다. 동등한 `generic_cli` 설정이 항상 존재한다 — 프로세스 계약은 같고, 벤더 어댑터가 더 아는 것은 **usage 의 위치·형태 해석과 (claude 만) allowlist 플래그**뿐이다.

### vendor-neutrality 강제 규칙

> 어떤 agent CLI를 `generic_cli` 설정으로 표현할 수 없다면, 그것은 그 CLI의 문제가 아니라 **이 프로토콜의 결함**이다.

벤더 어댑터가 핵심 계약을 바꾸는 것은 금지한다. 벤더 어댑터를 제거해도 `generic_cli`로 같은 CLI를 구동할 수 있어야 한다. 벤더 SDK를 커널 어디에도 노출하지 않는다.

---

## Outbox 규약

```
<system temp>/harness/<repo-key>/<run-id>/<task-id>/outbox/attempt-<n>/
  result.json        # claim
  handoff.json
  attachments/
```

- attempt마다 **새 디렉토리**를 만든다. 이전 attempt의 산출물이 다음 판정에 섞이지 않는다.
- **워크스페이스 밖이고 `.harness/` 밖이다.** 저장소 밖이므로 agent가 여기에 쓴 것은 `git diff`에 나타나지 않는다. claim 파일이 diff를 오염시키지 않는 이유다.
- agent는 경로를 `$HARNESS_OUTBOX` 환경변수로 받고, **하네스가 dispatch 시점에 프롬프트 말미에 붙이는 절대 경로**로도 받는다. 도구 allowlist 가 좁은 CLI agent 는 자기 환경변수를 읽을 수 없어 환경변수만으로는 경로를 모른다.
- 전체 크기 상한을 둔다. 초과분은 잘라내고 `protocol_violation`으로 기록한다.
- 하네스가 읽어 정규화·검증한 뒤에만 `.harness/runs/<run-id>/tasks/<task-id>/`로 승격한다. **agent 산출물이 control-plane에 직접 들어가는 경로는 없다.**

---

## Conformance 스위트

`adapters/conformance.py`는 모든 어댑터에 동일한 테스트를 돌린다. **신규 어댑터의 합격 조건은 이 스위트 통과다.**

### 어댑터의 의무

| 항목 | 검사 |
|---|---|
| cwd 고정 | 프로세스의 cwd 가 `request.workspace` 와 일치한다 |
| **어댑터 자신의 쓰기 범위** | 어댑터는 `workspace` 와 `outbox` 밖에 쓰지 않는다. 결과 아티팩트는 `outbox` 에만 둔다 |
| env 화이트리스트 | `request.env` 에 없는 변수를 자식 프로세스에 넣지 않는다 |
| timeout | `timeout_s` 초과 시 프로세스를 종료하고 `runtime_failure=timeout` 을 채운다 |
| 비정상 종료 | 시그널 kill 시에도 `AgentResult` 를 반환한다. 예외를 밖으로 던지지 않는다 |
| 아티팩트 부재 | claim/handoff 가 없으면 해당 경로를 `None` 으로 반환한다. 만들어내지 않는다 |
| 아티팩트 파손 | 내용이 깨져 있어도 **경로만 반환한다.** 파싱하거나 고치지 않는다 |
| 판정 금지 | `AgentResult` 어디에도 성공/실패 해석이 없다 |
| 멱등 정리 | 같은 attempt 로 두 번 호출해도 프로세스 잔재가 남지 않는다 |
| container 감싸기 | 프로세스를 띄우는 어댑터는 `request.container` 가 있으면 05 의 조립 규칙으로 자기 argv 를 감싼다. 규칙을 변형하지 않는다 |

### 어댑터의 의무가 **아닌** 것

**agent가 워크스페이스에 쓰는 것은 정상적인 작업이다.** implementation agent는 `allowed_paths` 안의 파일을 만들고 고친다. 이것을 어댑터가 막지 않는다.

`allowed_paths` / `forbidden_paths` 준수 여부는 어댑터가 아니라 **`exec/verify`의 사후 diff 판정**이 결정한다. 06 참조.

`readonly` task와 `safe` 프로파일에 한해서만 conformance가 "diff 가 비어야 함"을 검사한다.

이 검사는 **탐지**이지 OS 수준 강제가 아니다. 05의 보장/미보장 표와 동일한 표현 규약을 따른다.

---

## preflight 실패 분류

`preflight()`는 agent를 실행하기 전에 어댑터가 사용 가능한지 확인한다. 실패의 종류가 verdict를 가른다.

| `kind` | 예 | verdict |
|---|---|---|
| `missing_prerequisite` | CLI 미설치, 로그인 필요, credential 없음 | **`blocked`** |
| `misconfigured` | 어댑터 설정 schema 오류, placeholder 미해석 | **`error`** |
| `internal_error` | 내부 예외, 프로토콜 위반 | **`error`** |

canonical 실패 분류표는 10에 있다. 여기서는 어느 `kind`가 어느 분류에 대응하는지만 정한다.

`harness doctor`는 등록된 모든 어댑터의 `preflight()`를 실행하고 같은 기준으로 보고한다.

---

## exit code에 대한 어댑터의 입장

어댑터는 exit code를 **수집만** 한다.

> `exit_code == 0` is not success. `exit_code != 0` is not necessarily implementation failure.

- `timeout`, 시그널 kill, 프로토콜 위반은 **실행 자체가 유효하게 성립하지 않은 것**이므로 `runtime_failure`에 표시한다. 이것만이 어댑터가 하는 유일한 해석이다.
- 그 밖의 non-zero exit는 해석 없이 그대로 반환한다. 판정은 06이 한다.
