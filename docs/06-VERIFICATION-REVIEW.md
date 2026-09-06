# 06 · Verification and Review

This document holds the **canonical definition of the adjudication algorithm and Command Policy**.

## The Principle of Adjudication

> Agents propose. Harness verifies. **Evidence decides.**

What is used for adjudication is only the observations the harness itself produced. An agent's claim, the exit code, and the presence or absence of outputs cannot become a conclusion in themselves.

---

## Command Policy

> **Agent-authored commands are untrusted input. Harness must authorize them before execution.**

A task definition can be written by a planner agent, and repository content can be contaminated. Every command the harness executes must go through the policy before execution.

### What It Applies To

- acceptance criteria commands
- `kind: command` preconditions
- project health commands (the ones `converge` executes)
- commands a verifier or plugin intends to execute
- commands an eval fixture intends to execute

**There are no exceptions.** Every point at which the harness spawns a subprocess goes through `policy.py`.

### Configuration

```yaml
command_policy:
  default: require_approval          # fail-closed
  rules:                             # first match wins
    - {match: '^(npm|pnpm|yarn) (test|run (build|lint|typecheck))$', verdict: allow}
    - {match: '^(pytest|python -m pytest)', verdict: allow}
    - {match: '^git (status|diff|log)', verdict: allow}
    - {match: 'rm\s+-rf|git\s+push\s+--force|git\s+reset\s+--hard|^sudo|DROP\s+DATABASE|id_rsa', verdict: deny}
    - {match: '^(curl|wget|npm install|pip install|terraform apply|kubectl apply)', verdict: require_approval}
```

What is matched is the normalized string produced by joining argv with spaces.

`harness init` writes this rule list into the default config. What has been approved for automatic execution must be knowable by opening the file, and the project examines and fixes it.

If the `command_policy` key is absent there are no rules at all, so every command takes `default`. **Fail-closed applies to the absence of the key as well.**

### Adjudication Order

```
1. turn argv into a normalized string
2. scan rules from the top and adopt the verdict of the first match
3. if there is no match, adopt default (fail-closed = require_approval)
4. record a command_policy_decision event
5. handling per verdict
```

| verdict | Handling |
|---|---|
| `allow` | It is executed |
| `deny` | It is not executed. `analyze` should have caught it in advance, so reaching runtime is a task-definition defect → state `needs_replan` (reason: `policy_denied_at_runtime`) |
| `require_approval` | On a TTY, interactive approval. Otherwise, look up `.harness/approved_commands.yaml`. Unapproved in unattended execution is verdict `blocked` |

### Approval Storage Format

```yaml
# .harness/approved_commands.yaml
approvals:
  - cmd: ["npm", "install"]
    hash: "sha256:..."        # hash of the normalized string
    approver: "emdhks09@gmail.com"
    approved_at: "2026-08-27T14:20:00+09:00"
    scope: run                # run | project
```

Because the hash is the key, if even one argument changes the approval is not reused.

### The Default Execution Is `shell=False`

The argv list is passed as-is to `subprocess.run(argv, shell=False)`. Shell metacharacters, command substitution, pipes, and redirection are not interpreted, so the most common path around string matching is closed.

A command that genuinely needs a shell must declare `shell: true` explicitly, and **the declaration itself is automatically promoted to `require_approval`.**

### The Honest Limits

**Regex matching is not a security boundary.**

This is a guardrail against a wrong planner and repository injection. The real boundary is the execution profile, and that is `container` only. See 05's guarantee / non-guarantee table.

The meaning of `allow` and its limits are described in 09.

---

## Acceptance Criteria Lifecycle

```
[1] baseline   before the agent executes, the harness executes them itself
[2] agent execution
[3] post       every AC is executed again
```

### baseline

Each AC is executed, classified as `green_before` or `red_before`, and an `ac_baseline_executed` event is recorded.

| Situation | Handling |
|---|---|
| `expect_fail_before: true` but `green_before` | **`ac_not_discriminating`.** It passes no matter what is changed, so it has no discriminating power. **Without executing the agent,** state `needs_replan` |
| `red_before` without `expect_fail_before` | `pre_existing_failure`. It goes onto the ledger as a `debt_opened` event |

`expect_fail_before` is a declaration that demands a red→green proof. If it already passes at baseline, that AC proves nothing about this task.

### Execution Environment

- cwd is the workspace (the worktree). baseline and post use the same cwd.
- The timeout is config's `ac_timeout_s` (default 300). Exceeding it is classified as `red` and the reason is recorded.
- exit code 0 = green, anything else = red. Because the harness runs the ACs, here the exit code is itself the observation.
- Every AC command goes through Command Policy.

---

## TDD Mode — the Harness Observes red→green

A task with `development.mode: tdd` (03) is **dispatched twice** within one attempt. An agent saying "I did TDD" is not evidence, so the harness observes it directly between the phases.

```
[1] baseline        only the ACs that are not expect_fail_before are executed
[2] test-author     the first dispatch — it writes only tests
[3] red gate        the baseline of the expect_fail_before ACs is measured here. All must be red
[4] implementation  the second dispatch — it only implements
[5] post            every AC is executed again (exactly the differential adjudication above)
```

**The baseline of an `expect_fail_before` AC is measured at [3].** A red at a point where the test does not exist proves nothing. Only the observation that the test exists and fails is the before of a red→green. The baseline is split into two pieces, but their union is still one observation per AC, so both differential adjudication and resume (10) follow the same procedure as standard mode.

**If the red gate is not passed, the implementation is not dispatched.**

### Phase Scope

Each phase's diff is computed against that phase's observation base and receives exactly the path scope adjudication of 05.

| Phase | Observation base | `allowed` | Added to `effective_forbidden` |
|---|---|---|---|
| test-author | HEAD at dispatch time (`base` of `task_dispatched`) | `test_paths` | `implementation_paths` |
| implementation | the red gate commit (`base` of `tdd_phase_completed`) | `implementation_paths` | `test_paths` |

- Even if the two lists are declared so that they overlap, **the opposite list is forbidden** in each phase, so no hole opens.
- When the test-author phase ends, the harness commits its changes onto the task branch and leaves that sha as the observation base of the next phase. Because every later observation is against this sha, **the same diff is observed even if the agent hides the change in a commit.** That a test's modification or deletion becomes a `path_violation` in the implementation phase follows from this.
- The whole attempt's diff adjudication (the `kind` expectation, `allowed_paths`, the review tier) is done over the sum of the two phases, exactly as before. **TDD only adds evidence; it does not replace any existing adjudication.** Changes made by a fixer must pass the same phase scope again.

### Gate Failure

| Situation | reason | verdict | next_state |
|---|---|---|---|
| test-author left its scope (implementation paths included) | `path_violation` | `rejected` | per the retry rule |
| test-author wrote nothing | `tdd_no_tests` | `rejected` | per the retry rule |
| an AC is green at the red gate | `ac_not_discriminating` | — | `needs_replan` |
| there is not a single `expect_fail_before` AC | `ac_not_discriminating` | — | `needs_replan` |
| an AC of the red gate is `deny` | `policy_denied_at_runtime` | — | `needs_replan` |
| an AC of the red gate was not executed because it is unapproved | `unapproved_command` | `blocked` | `human_required` |
| an AC of the red gate could not be executed (timeout, process failure) | `tdd_red_gate_unexecuted` | `error` | per the retry rule |
| the implementation modified or deleted a test, or left its scope | `path_violation` | `rejected` | per the retry rule |
| still red after the implementation | `unmet` | `rejected` | per the retry rule |
| an AC that was green became red | `regression` | `rejected` | per the retry rule |
| the test-author changes could not be pinned as the red base commit | `tdd_checkpoint_failed` | `error` | per the retry rule |
| a profile without worktree separation (`safe`) | `tdd_requires_worktree` | `error` | per the retry rule |
| the mode is TDD but the installation has no option to execute that phase | `tdd_mode_unavailable` | `error` | per the retry rule |

`ac_not_discriminating` is used as is because the meaning is the same — an AC that passes even though the test exists passes no matter what is changed, so it has no discriminating power. There is no reason to invent a new state.

`tdd_requires_worktree` and `tdd_mode_unavailable` are `error` following the boundary of 10. They are not a problem that is solved by a human preparing the environment; they are a configuration defect where the combination of the task declaration and the installation does not hold.

### Where the Evidence Lives

| Question | journal |
|---|---|
| did the test-author phase end | `tdd_phase_completed {phase: test_author, base}` |
| was the red gate passed | `tdd_phase_completed {phase: red_gate, ok}` |
| what was the red evidence | the `ac_baseline_executed {classification: red_before}` in between |
| was the implementation executed | `tdd_phase_completed {phase: implementation, ok: true}` after `agent_finished` |
| what is the green gate result | `ac_post_executed {differential: proven \| unmet}` |

No separate event is kept for the green gate. The result of the differential adjudication is the green gate, and metrics are a projection of the journal, not separate instrumentation (03).

---

## Normalization — the Stage Before Adjudication

```
raw claim / raw handoff found in the outbox
        │
        ├ jsonschema validation passes → promoted to claim.json / handoff.json
        │                                claim_received / handoff_received events
        │
        └ validation fails             → preserved as *.invalid.json
                                         claim_rejected / handoff_rejected events
                                         treated as None in later adjudication
```

> **Invalid claim is an invalid report, not automatically an invalid implementation.**

If a present artifact cannot be read or decoded as UTF-8, normalization records a
diagnostic (source path and read error) in `*.invalid.json` instead of promoting
it or aborting the run. The original remains untouched. It has no usable payload
for adjudication or the required handoff gate, just like other invalid reports.

Normalization is not adjudication. There is no path by which a verdict is decided here.

---

## Differential Adjudication — a Task Is Evaluated Only by What It Changed

| baseline | post | Task adjudication |
|---|---|---|
| green | green | Normal |
| green | red | **Regression → `rejected`** |
| red (`expect_fail_before`) | green | **red→green proof succeeds** |
| red (`expect_fail_before`) | red | Falls short of the goal → `rejected` |
| red (pre-existing) | red | Not this task's responsibility. **No effect on adjudication.** The debt remains |
| red (pre-existing) | green | Incidentally resolved → `debt_closed` |

**A correct task is not rejected because of an unrelated pre-existing failure.**

There is one exception. If the pre-existing failure is inside that task's `allowed_paths`, it is not exempted; it is promoted to a review finding. Pretending not to see a broken test in one's own territory is not normal work.

---

## Conditions for `verified`

The following four must **all** pass. **The claim and the handoff are not here.**

1. The diff expectation for the `kind` is satisfied — `implementation` is not empty, `readonly` is empty, `analysis` is unconstrained
2. The changes are inside `allowed_paths` and outside `effective_forbidden` (see 05)
3. There is no `rejected` reason in the differential adjudication table above
4. The reviews the `effective_risk` tier requires have zero blocking findings

The reference point for observing the diff is **HEAD at dispatch** (`task_dispatched`'s `base`). Whether the agent committed the changes or left them in the working tree, the same diff is observed — whether it committed has no effect on adjudication (05).

**No path leads to `rejected` merely because the claim is broken or missing.**

When an `implementation` task has an empty diff and every AC passes, it is not passed silently. It is recorded as `no_op_detected` and sent to state `needs_replan`. This combination is a signal that the ACs have no discriminating power.

---

## The terminal Stage — the handoff Gate and Integration

`verified` is not recorded immediately merely because evidence conditions 1–4 passed. **`verified` is recorded only at terminal.** The order is fixed.

```
evidence conditions 1-4 pass
   │  (no verdict recorded yet)
   ▼
required handoff gate
   ├ outputs.required is empty            → to integration
   ├ required handoff is valid            → to integration (TaskOutput merge recorded)
   └ required handoff missing or invalid  → no verdict recorded, next_state = repairing
   │
   ▼
integration  (only for profiles that use a worktree. Branch structure is in 05)
   ├ merge succeeds                       → verdict = verified
   └ merge conflict                       → no verdict recorded, next_state = integration_conflict
```

**This last point is the only place where `verified` is recorded.** That is what keeps 03's rule that `verdict_assigned` happens once per attempt. Putting the merge after the verdict would mean recording a second verdict for the same attempt on a conflict.

What `repairing` does:

- **The code is left as it is.** It is neither reverted nor rebuilt. The implementation has already been verified by evidence.
- A narrow fixer that regenerates **only the handoff artifact** is invoked. `fixer_dispatched {scope: "handoff"}`.
- It shares one prompt template with the existing fixer path. No new machinery is built.
- Success → verdict `verified` → `done`.
- Attempts exhausted → state `human_required` (reason: `handoff_missing`).

If an `optional` output is absent, it is simply excluded from the next task's context. No adjudication is made.

---

## exit code

> `exit_code == 0` is not success. `exit_code != 0` is not necessarily implementation failure. **Evidence decides.**

| Situation | Handling |
|---|---|
| timeout / signal kill / protocol violation | The execution itself did not validly stand. It is marked as `AgentResult.runtime_failure`, classified as verdict `error`, and goes through the retry policy |
| An ordinary non-zero exit | **It is only one piece of evidence.** The exit code and the stderr tail are recorded in an `agent_exit_nonzero` event, but if all harness-owned evidence passes, `verified` is possible |
| exit 0 | It is not a basis for success. Adjudication is done only by the algorithm above |

Derived verdicts such as `verified_with_warning` are not created. A non-zero exit is recorded in the journal and shown in the report, but it does not contaminate the verdict.

---

## `blocked` Adjudication — Harness-Owned Evidence Only

```
1. precheck
   the harness executes the preconditions and the adapter preflight itself.
   missing_prerequisite       → verdict blocked (the agent is not executed)
   misconfigured / internal   → verdict error

2. reclassification of a failure after execution
   the environment verifier judges from harness-owned evidence.
   · match AC stderr against config's blocked_signals patterns
   · execute the preconditions again
   · if confirmed by an independent probe → verdict blocked

3. the claim's blocked_hint
   used only as a trigger to execute that probe first.
   if the probe passes, the hint is not accepted
   → verdict rejected + claim_uncorroborated event
```

**No path reaches `blocked` on the agent's word alone.**

`blocked` blocks only that task and its downstream dependents. It does not abort the entire run.

The canonical boundary between `blocked` and `error` is 10's failure classification table.

---

## effective_risk

```
effective_risk = max(declared_risk, path_floor, diff_floor)
```

- `path_floor` — comes from the path patterns of `risk_rules` (the glob dialect in 05). Up-front it applies to `allowed_paths`, post-hoc to the actual diff paths.
- `diff_floor` — comes from the size of the actual diff. **400 or more changed lines in total, or 20 or more changed files, is `medium`.** Signals such as a new dependency or public API surface are caught by `risk_rules` as far as they are expressed as paths — that is why dependency manifests are in the default list.
- A task that does not declare `risk` is taken as declared `trivial`. The floor is the safety net.

**It is computed in two steps.**

1. **Up-front** — the floor is taken from the declared value and `allowed_paths` to decide scheduling and budget.
2. **Post-hoc** — **the review tier is fixed after looking at the actual diff.**

With the up-front value alone, a task declared `trivial` escapes review even when it touches authentication code. Only escalation is possible; a downgrade is made only by a recorded human waiver. Escalation is recorded as a `risk_escalated` event.

### risk_rules

```yaml
risk_rules:                      # a declaration is **added** to the built-in default list
  - {match: "src/payments/**", floor: high}
```

The built-in default list — authentication, cryptography, secrets, migrations, CI workflows, container definitions, dependency manifests:

```yaml
- {match: "**/auth/**", floor: high}
- {match: "**/*secret*", floor: high}
- {match: "**/*password*", floor: high}
- {match: "**/*credential*", floor: high}
- {match: "**/migrations/**", floor: high}
- {match: ".github/workflows/**", floor: high}
- {match: "**/Dockerfile*", floor: high}
- {match: "**/docker-compose*", floor: high}
- {match: "**/requirements*.txt", floor: high}
- {match: "**/pyproject.toml", floor: high}
- {match: "**/package.json", floor: high}
- {match: "**/go.mod", floor: high}
- {match: "**/Cargo.toml", floor: high}
```

A declaration **cannot turn the defaults off.** Since effective_risk is the max over the whole list, additions can only escalate. A downgrade is by a recorded human waiver alone.

### Tiers

| Tier | Reviewers |
|---|---|
| `trivial` | none — verification only |
| `low` | `spec` |
| `medium` | + `quality` |
| `high` | + `architecture-security` |
| `critical` | + `adversarial` |

---

## Bounded review wave

The default for `max_review_waves` is 2 (a config key of 06).

```
wave n:
  execute each of the tier's reviewers independently (fresh context — whether they are parallel is an implementation detail)
  merge findings + remove duplicates (key: rule · file · line)
  if blocking findings are 0 → to the handoff gate
  otherwise invoke 1 fixer (fresh context: findings + the files concerned + the task contract only)
  re-execute AC post + re-adjudicate diff and paths — changes made by review must also pass the same evidence standard
  re-review in the next wave
if blocking remains after the wave limit is exceeded → verdict rejected (10's review row)
```

- **Reviewers do not see the implementer's conversation.** The context is the diff, the task contract, and the constitution, and nothing else. A review that is persuaded by the implementer's reasoning is not an independent review.
- The `blocking` adjudication is deterministic: `severity ∈ {high, critical}` or `rule ∈ constitution.critical`. Reviewers do not decide blocking for themselves. **The list items of the constitution's `## critical` section are that rule list.**
- Exceeding the budget does not silently lower the quality bar. It stops with verdict `budget_exhausted`.

### The Reviewer's Output — findings

A reviewer is an agent, and it writes `findings.json` into its own outbox.

```json
{"schema": "harness.findings/v1", "task_id": "T-003",
 "findings": [{"severity": "high", "rule": "hardcoded-secret",
               "file": "src/db.py", "line": 12, "message": "a secret is in the code"}]}
```

- `severity` is `info | low | medium | high | critical`.
- **If the findings file is missing or violates the schema, that review does not stand** — verdict `error` (10's system defect). A reviewer's silence is not read as a pass. With no findings, write an empty array.
- The original of each wave is preserved as `review/wave-<n>/<reviewer>.json` (03's file layout).

---

## Budget Caps

This is the cap on the resources a run can use. **Exceeding it does not silently lower the quality bar** — the task at that point is assigned verdict `budget_exhausted`, and later tasks hit the same check, so the run stops. The run itself terminates normally and is left in the summary (10).

```yaml
budget:
  max_wall_time_s: null       # wall-clock time from the start of the run. null is unlimited
  max_agent_calls: null       # number of agent process invocations — reviewers and fixers included
  max_cost_usd: null          # valid only on adapters that report usage. It does not estimate (11)
```

- The check point is **immediately before every agent process is invoked** — the two dispatches of TDD,
  reviewers, fixers, and handoff repair are each checked separately.
- Every check records a `budget_checkpoint` event. Consumption is entirely a projection of the journal — there is no separate counter.
- If no cap is configured at all, no check is made and no event is recorded.

---

## The Largest Remaining Risk

**If the ACs are weak the harness cannot know the truth either.** The harness cannot be smarter than its ACs.

There are three mitigations.

1. The discriminating-power check at baseline (`ac_not_discriminating`)
2. `analyze` **reviews the ACs themselves, not the code** (08)
3. 11's `escape_rate` **quantifies** this risk — the rate at which the harness said `verified` but the hidden grader saw a failure

---

## Config Keys Owned by 06

03's canonical `config.yaml` fixes only the skeleton of the top-level keys. The definitions of the keys below belong to this document.

```yaml
ac_timeout_s: 300             # timeout of one AC (seconds)
agent_timeout_s: 1800         # timeout of one agent process (seconds)

max_attempts: 2               # how many times one task can retry on rejected/error
max_handoff_repairs: 1        # how many times repairing can invoke the handoff fixer

blocked_signals: []           # list of patterns (regex) to match AC stderr against

max_review_waves: 2           # the limit of the bounded review wave
adversarial_adapter: null     # name of the adapter the adversarial reviewer uses. null means the task's adapter

risk_rules: []                # path floors of effective_risk — the "risk_rules" section above

budget:                       # the "Budget Caps" section above
  max_wall_time_s: null
  max_agent_calls: null
  max_cost_usd: null

command_policy:               # the shape of the "Command Policy" section above
  default: require_approval
  rules: []
```

- `agent_timeout_s` is the source of `AgentRequest.timeout_s`. The adapter marks an overrun as `runtime_failure=timeout`, and 04 is canonical.
- **`max_attempts` is the criterion for "attempts exhausted" in 03's verdict → next_state table.** Once exhausted, the next_state of `rejected` becomes `needs_replan` instead of `ready`.
- Exhausting `max_handoff_repairs` gives state `human_required` (reason: `handoff_missing`).
- **`adversarial_adapter` is an option that raises the independence of the `critical` tier one step further.** If the same model as the implementer examines its own results adversarially, it shares the same blind spots. If another
  name declared in `adapters` is given, only the `adversarial` reviewer executes with that adapter. The remaining reviewers and the fixer are
  unaffected. If the given name is not in `adapters`, it is a load failure.
- **The empty default for `blocked_signals` is deliberate.** Planting patterns in advance produces false positives per project, and the result of a false positive is a wrong `blocked`. The default path is the harness-owned evidence of re-executing the preconditions, and patterns are added when that repository knows its own failure modes.
