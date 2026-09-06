# 04 · Agent Adapter

This document holds the **canonical definition of the adapter contract**.

## What the adapter does and does not do

The adapter goes no further than **spawning the process and reporting where the results are**.

| What the adapter does | What the adapter does not do |
|---|---|
| Execute the process with cwd fixed to `workspace` | Parse and validate claim/handoff |
| Deliver the `outbox` path to the agent | Adjudicate success or failure |
| Enforce timeout, clean up the process | Run the ACs |
| Collect exit code, stdout, stderr | Interpret the diff |
| Return artifact **paths** | Interpret artifact **contents** |
| Report usage if it can report it | Decide on a retry |

**The harness does all the parsing and adjudication.** Once the adapter starts interpreting results, adjudication differs from vendor to vendor and principle 1 collapses.

---

## Protocol

```python
@dataclass(frozen=True)
class AgentRequest:
    task_id: str
    prompt: str
    workspace: Path              # the agent's cwd — a worktree outside the repository
    outbox: Path                 # outside the workspace. result.json / handoff.json go here.
    profile: ExecutionProfile
    allowed_tools: list[str] | None
    timeout_s: int
    env: Mapping[str, str]       # allowlist. Includes HARNESS_OUTBOX.
    attempt: int
    container: ContainerSpec | None   # the container profile in 05. If None, executes on the host.


@dataclass(frozen=True)
class AgentResult:
    exit_code: int
    stdout: str
    stderr: str
    raw_claim_path: Path | None       # the harness does the parsing and validation
    raw_handoff_path: Path | None
    duration_s: float
    usage: Usage | None               # None if the vendor does not report it
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

The absence of a field like `success` on `AgentResult` is deliberate. The adapter does not judge success.

---

## Extension path

```
mock  →  generic_cli  →  claude_cli / codex_cli
```

**`mock`** — Deterministic. The scenario specifies the exit code, the output files, and the delay. The scenario is written directly in the adapter options, or given as a file path with `scenario`. It is the default for regression eval and CI, and it needs neither an LLM nor a network.

**`generic_cli`** — Invokes an arbitrary CLI from configuration alone.

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

**Vendor adapters** — `claude_cli`, `codex_cli`. They are a thin configuration on top of `generic_cli` and add **optional capabilities only**.

### `claude_cli`

```yaml
adapters:
  claude:
    type: claude_cli
    binary: claude               # the default. An argv prefix list is also allowed (for wrappers and tests)
    extra_args: []               # appended to argv as-is
    env_passthrough: [PATH, HOME]
    timeout_grace_s: 10
```

- Invocation: `<binary> -p --output-format json <extra_args...> --add-dir <outbox>` — the prompt is given on **stdin**. Because the outbox is outside the workspace (Outbox convention), claude cannot write there without `--add-dir` — even with `--permission-mode acceptEdits`, writes outside cwd are denied.
- **usage reporting** — parses the whole stdout as JSON and reads `usage.input_tokens` / `usage.output_tokens` / `total_cost_usd`. If parsing fails, usage is `None`. It does not estimate.
- **Tool allowlist** — if `request.allowed_tools` is present, `--allowedTools <comma-joined>` is added to argv. No code path in the current kernel populates this field — defining the source (the task contract or config) is not this document's contract, and until it is settled the value is always `None`.
- Session reuse is not supported — fresh context per task is the principle (principle 2 of 00).

### `codex_cli`

```yaml
adapters:
  codex:
    type: codex_cli
    binary: codex                # the default. An argv prefix list is also allowed
    extra_args: ["--sandbox", "workspace-write"]
```

- Invocation: `<binary> exec --json <extra_args...> --add-dir <outbox> -` — the prompt is given on **stdin**. The adapter adds the current attempt's outbox as a writable directory because it is outside the workspace (Outbox convention).
- Set `extra_args: ["--sandbox", "workspace-write"]` for coding tasks. The adapter leaves the sandbox selection to configuration; without that setting, Codex's default read-only sandbox does not allow implementation edits. `--add-dir` does not turn a read-only sandbox into a writable one. See the [official non-interactive guide](https://learn.chatgpt.com/docs/non-interactive-mode).
- **usage reporting** — attempts to parse each line of stdout as JSON and reads from the **last** line that carries a `usage` object (`input_tokens`/`output_tokens`). Cost is `None` because the vendor does not report it.
- Tool allowlist and session reuse are not supported.

For both adapters, a binary not found at preflight is `missing_prerequisite`. Every other contract — cwd, outbox, timeout, the env allowlist, artifact paths — is literally the same as `generic_cli`'s, and the conformance suite proves it. An equivalent `generic_cli` configuration always exists — the process contract is the same, and all the vendor adapter knows in addition is **the interpretation of usage's location and shape, the outbox access flag, and (claude only) the tool allowlist flag**.

### vendor-neutrality enforcement rule

> If some agent CLI cannot be expressed as `generic_cli` configuration, that is not a problem of that CLI but **a defect of this protocol**.

A vendor adapter changing the core contract is forbidden. Removing a vendor adapter must still leave `generic_cli` able to invoke the same CLI. No vendor SDK is exposed anywhere in the kernel.

---

## Outbox convention

```
<system temp>/harness/<repo-key>/<run-id>/<task-id>/outbox/attempt-<n>/
  result.json        # claim
  handoff.json
  attachments/
```

- Each attempt gets a **new directory**. Outputs from a previous attempt do not bleed into the next adjudication.
- **It is outside the workspace and outside `.harness/`.** Because it is outside the repository, what the agent writes here does not appear in `git diff`. That is why claim files do not contaminate the diff.
- The agent receives the path in the `$HARNESS_OUTBOX` environment variable, and also as **an absolute path the harness appends to the end of the prompt at dispatch**. A CLI agent with a narrow tool allowlist cannot read its own environment variables, so the environment variable alone does not tell it the path.
- Along with the path, the prompt tells the agent **the shape of the claim and handoff envelope** — the two examples in 03 are canonical. An agent that does not know the shape writes it its own way, and the result becomes `*.invalid.json`. That has no effect on adjudication (the claim is optional), but the handoff the next task would use is discarded every time.
- There is a cap on total size. The excess is truncated and recorded as `protocol_violation`.
- The harness promotes it to `.harness/runs/<run-id>/tasks/<task-id>/` only after reading, normalizing, and validating it. **There is no path by which agent output enters the control-plane directly.**

---

## Conformance suite

`adapters/conformance.py` runs the same tests against every adapter. **A new adapter's pass condition is passing this suite.**

### The adapter's obligations

| Item | Check |
|---|---|
| cwd fixed | The process's cwd matches `request.workspace` |
| **The adapter's own write scope** | The adapter does not write outside `workspace` and `outbox`. It puts result artifacts in `outbox` only |
| env allowlist | It does not put a variable absent from `request.env` into the child process |
| timeout | On exceeding `timeout_s` it terminates the process and fills in `runtime_failure=timeout` |
| Abnormal termination | It returns an `AgentResult` even on a signal kill. It does not raise an exception outward |
| Artifact absence | If the claim/handoff is absent it returns that path as `None`. It does not create one |
| Artifact corruption | Even when the content is corrupted it **returns the path only.** It does not parse or fix it |
| Adjudication forbidden | Nowhere in `AgentResult` is there an interpretation of success or failure |
| Idempotent cleanup | Calling it twice with the same attempt leaves no leftover process |
| container wrapping | An adapter that spawns a process wraps its own argv with the assembly rule in 05 when `request.container` is present. It does not alter the rule |

### What is **NOT** the adapter's obligation

**The agent writing into the workspace is normal work.** An implementation agent creates and fixes files inside `allowed_paths`. The adapter does not block this.

`allowed_paths` / `forbidden_paths` compliance is decided by **`exec/verify`'s post-hoc diff adjudication**, not by the adapter. See 06.

Only for `readonly` tasks and the `safe` profile does conformance check that "the diff must be empty".

This check is **detection**, not OS-level enforcement. It follows the same phrasing convention as the guarantee/non-guarantee table in 05.

---

## preflight failure classification

`preflight()` checks whether the adapter is usable before executing the agent. The kind of failure decides the verdict.

| `kind` | Example | verdict |
|---|---|---|
| `missing_prerequisite` | CLI not installed, login required, no credential | **`blocked`** |
| `misconfigured` | Adapter configuration schema error, uninterpreted placeholder | **`error`** |
| `internal_error` | Internal exception, protocol violation | **`error`** |

The canonical failure classification table is in 10. This document settles only which `kind` corresponds to which class.

`harness doctor` executes `preflight()` for every registered adapter and reports by the same criteria.

---

## The adapter's position on exit code

The adapter **only collects** the exit code.

> `exit_code == 0` is not success. `exit_code != 0` is not necessarily implementation failure.

- `timeout`, a signal kill, and a protocol violation mean **the execution itself did not validly stand**, so they are marked in `runtime_failure`. This is the only interpretation the adapter makes.
- Any other non-zero exit is returned as-is, without interpretation. 06 does the adjudication.
