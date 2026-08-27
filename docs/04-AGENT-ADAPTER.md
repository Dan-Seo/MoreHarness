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

**`mock`** — 결정론적. 시나리오 파일로 exit code, 산출 파일, 지연을 지정한다. 회귀 eval과 CI의 기본값이며 LLM도 네트워크도 필요 없다.

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

**벤더 어댑터** — `claude_cli`, `codex_cli`. **선택적 능력만** 추가한다: usage 보고, 도구 화이트리스트, 세션 재사용.

### vendor-neutrality 강제 규칙

> 어떤 agent CLI를 `generic_cli` 설정으로 표현할 수 없다면, 그것은 그 CLI의 문제가 아니라 **이 프로토콜의 결함**이다.

벤더 어댑터가 핵심 계약을 바꾸는 것은 금지한다. 벤더 어댑터를 제거해도 `generic_cli`로 같은 CLI를 구동할 수 있어야 한다. 벤더 SDK를 커널 어디에도 노출하지 않는다.

---

## Outbox 규약

```
<system temp>/harness/<run-id>/<task-id>/outbox/attempt-<n>/
  result.json        # claim
  handoff.json
  attachments/
```

- attempt마다 **새 디렉토리**를 만든다. 이전 attempt의 산출물이 다음 판정에 섞이지 않는다.
- **워크스페이스 밖이고 `.harness/` 밖이다.** 저장소 밖이므로 agent가 여기에 쓴 것은 `git diff`에 나타나지 않는다. claim 파일이 diff를 오염시키지 않는 이유다.
- agent는 `$HARNESS_OUTBOX` 환경변수로 경로를 받는다.
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
