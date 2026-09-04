# 03 · Data Model

This document holds the **canonical definition of verdict and state**, and the **canonical definition of the event list**.

---

## Spec

```yaml
# specs/<slug>/spec.yaml
slug: user-api
intent: "Build a user CRUD API"
requirements:
  - id: R-001
    statement: "A user can be created with POST /api/users"
    rationale: "A premise of the sign-up flow"
    acceptance_hint: "Returns 201 and an id after creation"
  - id: R-002
    statement: "A duplicate email is rejected with 409"
open_questions:
  - "[NEEDS CLARIFICATION] soft delete or hard delete"
```

`R-###` is unique within the spec and is not reused. When a requirement is deleted the number is left as a gap. Reusing a number makes past runs' coverage records false.

If even one `[NEEDS CLARIFICATION]` remains in `open_questions`, `analyze` blocks the `run`.

---

## Task contract

```yaml
# tasks/T-003.task.yaml
id: T-003
name: api-layer
kind: implementation          # implementation | analysis | readonly
satisfies: [R-002, R-005]
depends_on: [T-001]
risk: medium                  # trivial | low | medium | high | critical (the declared value)
agent: default
profile: worktree             # safe | worktree | container | unsafe

allowed_paths:   ["src/api/**", "tests/api/**"]
forbidden_paths: []           # applied as the union with the global forbidden list in config

preconditions:
  - {kind: env_var, name: DATABASE_URL}       # checks only for presence. The value is never recorded.
  - {kind: command, cmd: ["docker", "info"]}  # subject to Command Policy
  - {kind: file, path: ".env.local"}

context:
  docs:    ["docs/ARCHITECTURE.md#api-layer"]
  files:   ["src/types/user.ts"]
  symbols: ["UserRepository"]

acceptance:                   # the harness runs these itself. Subject to Command Policy.
  - cmd: ["npm", "run", "build"]
  - cmd: ["npm", "test", "--", "tests/api"]
    expect_fail_before: true

outputs:
  required: [public_api]      # agent-produced fields the next task's execution requires
  optional: [decisions]

spec_hash: "sha256:..."       # hash of the referenced spec. For drift detection.
```

### `cmd` is an argv list

A list, not a string. Because execution defaults to `shell=False`, shell metacharacters, command substitution, pipes, and redirection are not interpreted. If a shell is truly required, `shell: true` must be declared explicitly, and the declaration itself is automatically promoted to `require_approval` by Command Policy. See 06.

### `spec_hash`

It is the **sha256 of the bytes** of the spec file that holds the R-### in `satisfies`, prefixed with `sha256:`. `harness tasks` stamps it, and analyze and converge catch drift by comparing it against the current spec (08).

### What `kind` determines

| `kind` | diff expectation | Use |
|---|---|---|
| `implementation` | must not be empty | Code changes |
| `readonly` | must be empty | Investigation and checking |
| `analysis` | not constrained | Work whose only output is a handoff |

---

## Task Output — provenance and necessity

There are two axes for understanding `outputs`: **who produces it (provenance)** and **whether its absence is a problem (necessity)**.

| Field | Producer | Nature |
|---|---|---|
| `changed_files`, `created_files`, `diff_stat` | **the harness** (computed from git diff) | Always present. No agent cooperation is needed. **They cannot be listed in `required`.** |
| `public_api`, `decisions`, and other semantic summaries | **the agent** (handoff artifact) | `required` or `optional` |

- If `outputs.required` is empty — as it is for most tasks — the presence or absence of a handoff has no effect on adjudication.
- A value the harness computes cannot be missing, so it is not subject to `required`. The mistake of listing a harness-produced field in `required` is caught by `analyze` in advance.
- The final `TaskOutput` record is the merge of **harness-produced fields + handoff fields that passed validation**, and the harness does the merging and the recording.
- Even inside the merged result, **the provenance of harness-produced fields (fact) and agent-produced fields (untrusted) is marked distinctly.** See 07.

---

## Claim and handoff are separate files

They are different files in the outbox. If one is broken the other survives. That is because narrative and data have different consumers and different lifetimes.

### Claim envelope — optional, a hint

```json
{
  "schema": "harness.claim/v1",
  "task_id": "T-003",
  "outcome_claim": "implemented",
  "commands_run": [{"cmd": "npm test", "exit_code": 0}],
  "blocked_hint": "DATABASE_URL appears to be missing"
}
```

`outcome_claim` is `implemented | blocked | infeasible`.

That the field is named **`blocked_hint`** and not `blocked_reason` is deliberate. It is not a conclusion but a hypothesis for the harness to check, and if the harness cannot corroborate it with a probe, it is not accepted. See 06.

### Handoff artifact — the contract

```json
{
  "schema": "harness.handoff/v1",
  "task_id": "T-003",
  "public_api": ["POST /api/users", "GET /api/users/:id"],
  "decisions": ["Authentication is handled in middleware and route handlers assume it"]
}
```

If the `schema` field is absent or jsonschema validation fails, it is preserved as `*.invalid.json` and treated as `None` in later adjudication. **A broken report does not mean a broken implementation.**

---

## Verdict — canonical

There are **exactly five** verdicts.

| verdict | Meaning |
|---|---|
| `verified` | All harness-owned evidence passed and the required handoff gate passed too |
| `rejected` | The evidence shows it falls short of the goal (regression, red→green falls short, a blocking finding remains) |
| `blocked` | The system is fine, but an external prerequisite is absent so it cannot proceed |
| `error` | A harness or adapter defect is suspected, or the execution did not validly stand |
| `budget_exhausted` | Budget exhausted |

**`repairing`, `needs_replan`, `integration_conflict`, and `human_required` are not verdicts but states.**

---

## State — canonical

State is a **different axis**, marking the position in the workflow.

| state | Meaning |
|---|---|
| `pending` | Dependencies are not yet satisfied |
| `ready` | Waiting for dispatch |
| `precheck` | `preconditions` + the adapter `preflight` are executing. The agent has not executed yet |
| `running` | The agent process is executing |
| `executed` | The agent process exited. **Regardless of the exit code and of the presence or absence of a claim** |
| `verifying` | Normalization + AC post + diff and path adjudication are under way |
| `reviewing` | A review wave is under way |
| `repairing` | All implementation evidence passed, but the required handoff is absent or invalid. Regenerates **only the handoff artifact** |
| `needs_replan` | The task definition has to be fixed |
| `integration_conflict` | A merge conflict |
| `human_required` | Cannot proceed without human intervention |
| `done` | Finished. Reached only with verdict `verified` |

There is no `claimed` state. claim is optional, so the state machine cannot wait for a claim.

### State transitions

```
pending ─(dependency verified)─> ready ─> precheck ─> running ─> executed ─> verifying
                                              │                                  │
                                precheck fails│             if review is needed  ├─> reviewing ─┐
                                              │                                  │              │
                                              v                                  v              v
                                       blocked | error                    (handoff gate) <──────┘
                                                                                 │
                                            required handoff missing/invalid ────┤
                                                                          │      │
                                                                          v      v
                                                                  repairing  verdict = verified ─> done
```

When `precheck` fails the agent is not executed and verdict `blocked` or `error` is assigned. The classification criteria are in 10.

If `repairing` succeeds, verdict `verified` → `done`. If attempts are exhausted, `human_required` (reason: `handoff_missing`).

`blocked` blocks only that task and its downstream dependents. **It does not abort the entire run.** Branches of the DAG that are not blocked keep going, and the run terminates normally while summarizing what was blocked and why. See 10.

### verdict → next_state

| verdict | Condition | next_state |
|---|---|---|
| `verified` | — | `done` |
| `rejected` | attempts remain | `ready` |
| `rejected` | attempts exhausted | `needs_replan` |
| `blocked` | — | `human_required` |
| `error` | transient · room to retry | `ready` |
| `error` | system defect · repeated | `human_required` |
| `budget_exhausted` | — | `human_required` |

There are three cases that have only a state and no verdict.

| Situation | verdict | next_state |
|---|---|---|
| required handoff missing/invalid (the implementation evidence passed) | — | `repairing` |
| A merge conflict | — | `integration_conflict` |
| A task definition defect | — | `needs_replan` |

**A task definition defect goes to `needs_replan` regardless of how many attempts remain, because retrying is meaningless.** Three things belong here.

| reason | Situation |
|---|---|
| `policy_denied_at_runtime` | A `deny` command reached runtime (a case `analyze` missed) |
| `ac_not_discriminating` | An AC with `expect_fail_before: true` already passed at baseline |
| `no_op_detected` | It is an `implementation` task, but the diff is empty and every AC passed |

### Rules for assigning a verdict

- `verdict_assigned` occurs **at most once per attempt** and records the `attempt` number in the payload.
- Because the `rejected`/`error` → `ready` → retry path exists, a rule that a task has exactly one verdict overall does not hold.
- **A task's final verdict is defined as the `verdict_assigned` of the highest `attempt`.**
- `verified` is recorded only at terminal. It is not recorded immediately merely because the evidence conditions passed. The order is in 06.
- **In the three cases above that have only a state and no verdict, `verdict_assigned` is recorded as well.** `verdict` is `null` and `next_state` is filled in. This is the only event that carries `next_state`, so `state == fold(journal)` holds without adding an event.

---

## The journal is canonical, state is a projection

```
journal.jsonl   append-only · immutable · single writer   <- canonical
state.json      a snapshot of fold(journal)               <- derived cache
```

- `seq` increases monotonically from 1 and **has no gaps.**
- The event `id = <run_id>-<seq:06d>`.
- `state.json` holds `last_applied_seq`. The journal can be folded from the beginning at any time to reconstruct it.
- **The write order is journal append + fsync → state update.** If it dies in between, the snapshot merely lags; nothing is lost. See 10.
- Events are immutable. A correction is made by a **new event**, not by deletion or modification.
- **Even under parallel execution the journal writer is exactly one orchestrator process.** What is parallelized is agent subprocesses, not state recording.

`state == fold(journal)` is the invariant `harness doctor` checks.

### Common shape of an event record

```json
{"id": "run-20260827-1432-000042",
 "seq": 42,
 "ts": "2026-08-27T14:35:02.113+09:00",
 "type": "ac_post_executed",
 "run_id": "run-20260827-1432",
 "task_id": "T-003",
 "attempt": 1,
 "payload": {}}
```

`task_id` and `attempt` are `null` in run-level events.

### Event list — canonical

| type | When | Key payload |
|---|---|---|
| `run_started` | run starts | `manifest`, `profile`, `adapter`, `max_parallel` |
| `precondition_checked` | precheck | `kind`, `name`, `ok`, `detail` (**the value is never recorded**) |
| `command_policy_decision` | just before a command executes | `cmd`, `verdict`, `rule`, `approver` |
| `task_dispatched` | dispatch | `context_manifest_ref`, `prompt_ref`, `effective_risk`, `base` (HEAD at dispatch — the reference point for diff observation, see 06) |
| `agent_started` | the process starts | `adapter`, `workspace`, `outbox` |
| `agent_finished` | the process exits | `exit_code`, `duration_s`, `usage` (`null` when not reported), `runtime_failure`, `transcript_ref` |
| `agent_exit_nonzero` | exit code ≠ 0 | `exit_code`, `stderr_tail` |
| `claim_received` | claim passes normalization | `outcome_claim` |
| `claim_rejected` | claim violates the schema | `error`, `path` |
| `claim_uncorroborated` | a probe does not corroborate the claim's hint | `hint`, `probe`, `probe_result` |
| `handoff_received` | handoff passes normalization | `fields` |
| `handoff_rejected` | handoff violates the schema | `error`, `path` |
| `handoff_missing` | a required handoff is absent | `required`, `present` |
| `ac_baseline_executed` | before the agent executes | `cmd`, `exit_code`, `classification`, `expect_fail_before` |
| `ac_post_executed` | after the agent executes | `cmd`, `exit_code`, `classification`, `differential` |
| `debt_opened` | a pre-existing failure AC is found | `debt_id`, `cmd`, `origin_task` |
| `debt_closed` | that AC becomes green | `debt_id`, `closed_by` |
| `debt_waived` | ship accepts a waiver | `debt_id`, `approver`, `reason` |
| `path_violation` | the diff goes outside the allowed scope | `paths`, `rule` |
| `risk_escalated` | effective_risk escalated | `declared`, `path_floor`, `diff_floor`, `effective` |
| `review_finding` | a reviewer's finding | `wave`, `reviewer`, `severity`, `rule`, `file`, `line`, `blocking` |
| `fixer_dispatched` | a fixer is invoked | `wave`, `scope` (`code` or `handoff`) |
| `verdict_assigned` | the attempt ends | `verdict`, `attempt`, `reason`, `next_state` |
| `budget_checkpoint` | budget check | `tokens`, `cost_usd`, `wall_time_s`, `remaining` |
| `run_finished` | run finishes | `summary`, `open_debts`, `human_required` |

`classification` is `green_before | red_before` (baseline) or `green | red` (post).

`agent_finished`'s `usage` and `duration_s` are the source of all the cost and speed metrics in 11. **There is no separate instrumentation code.**

---

## File layout

```
<repo>/
  .harness/                       # the harness's own control-plane
    .gitignore                    # run outputs are not committed — init creates it
    config.yaml                   # the harness always reads it from the main repository
    constitution.md
    approved_commands.yaml
    waivers.yaml                  # ship's debt exemption — 08
    analyze.json  analyze-report.md          # analyze's gate record and report — 08
    converge.json  coverage.md  ship-report.md   # what converge and ship produce — 08
    knowledge/K-###.yaml
    runs/<run-id>/
      manifest.json
      journal.jsonl
      state.json
      tasks/T-003/
        context.manifest.json
        prompt.md
        claim.json      | claim.invalid.json
        handoff.json    | handoff.invalid.json
        verification.json
        review/wave-1/*.json
        transcript/<outbox name>.log   # one per dispatch — the agent's stdout/stderr
  specs/<slug>/spec.yaml
  tasks/T-###.task.yaml
  evals/fixtures/<case>/

<system temp>/harness/<repo-key>/<run-id>/<task-id>/
  worktree/                       # the agent's cwd — outside the repository
  outbox/attempt-<n>/
    result.json                   # claim
    handoff.json
    attachments/
```

`<repo-key>` is a key made from the repository's absolute path. Because run-ids are derived from the clock, two different repositories can produce the same value, and if worktrees are made on top of it they overwrite each other's work.

**Both the worktree and the outbox are outside the repository.** Putting the worktree inside `.harness/` makes the agent's cwd sit inside the control-plane, which directly contradicts "`.harness/` is not agent territory".

The harness **always reads `constitution.md` and `config.yaml` from the main repository and never from the worktree.** That is because the copy in the worktree is repository content the agent can modify.

`.harness/**` is always included in the global `forbidden_paths`.

A file name in `transcript/` is the **outbox directory name** that dispatch wrote — `attempt-2`,
`attempt-1-repair-1`, `attempt-1-review-1-spec`. One task is dispatched several times, so overlapping
names leave no way to tell which dispatch a log belongs to. The content is the stdout and stderr the
adapter collected, and the harness **does not use it for adjudication.** It is kept because post-hoc
diagnosis of an agent that failed silently (permission denial, turns exhausted) is impossible
otherwise. `agent_finished`'s `transcript_ref` carries the path.

What gets committed is only human-written input (`config.yaml`, `constitution.md`, `approved_commands.yaml`,
`waivers.yaml`, `knowledge/`), and not run outputs (`runs/`, the records of analyze, converge and ship).
`harness init` makes that boundary as `.harness/.gitignore`. If it already exists it is not
touched — a human has edited it.

### `config.yaml` — canonical

The control-plane configuration. `harness init` creates a default file of this shape.

```yaml
version: 1                    # only 1 is supported. Anything else fails to load.

defaults:
  adapter: mock               # must be a name declared in adapters
  profile: worktree           # safe | worktree | container | unsafe
  max_parallel: 1             # an integer of 1 or more. Parallel starts at M3.

allow_unsafe: false           # the config half of allowing the unsafe profile

adapters:                     # at least one. type is required and
  mock:                       # the remaining keys are that adapter's options — see 04
    type: mock
```

- If `defaults.adapter` is not in `adapters`, the load fails.
- `profile: unsafe` cannot be used without `allow_unsafe: true`. For the other half, the **explicit CLI flag**, 05 is canonical.
- **Unknown top-level keys are not rejected.** Later milestones add their own keys here, and the canonical definition of each key is in the document that owns that feature — `command_policy` · `ac_timeout_s` · `agent_timeout_s` · `max_attempts` · `max_handoff_repairs` · `blocked_signals` · `risk_rules` · `max_review_waves` · `adversarial_adapter` · `budget` are 06, `forbidden_paths` and `container` are 05, `context` is 07, and `health_commands` and `knowledge` are 08.
