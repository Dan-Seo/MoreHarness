# 10 · Failure and Recovery

This document holds the **canonical definition of the failure classification table**. Every other document references this table and does not restate it.

## Failure Classification — canonical

verdict and state are **different axes**. 03's definitions apply.

| Class | Criterion | Example | verdict | next_state | Retry |
|---|---|---|---|---|---|
| **prerequisite** | The system is fine, an external prerequisite is absent | CLI not installed, login required, no credential, Docker daemon off, a required file or env absent, an unapproved command during unattended execution | `blocked` | `human_required` | Resume after a human resolves it |
| **system defect** | A harness or adapter implementation problem is suspected | adapter config schema error, internal exception, protocol violation, unexpected result shape, conformance violation | `error` | `ready` → `human_required` on repetition | Limited retry according to policy |
| **transient** | A transient execution failure | timeout, signal kill, transient network | `error` | `ready` | Retry with backoff |
| **verification** | AC or differential adjudication failure | regression, red→green falls short | `rejected` | attempts remain `ready` / exhausted `needs_replan` | Up to the attempt limit |
| **review** | A blocking finding remains | | `rejected` | attempts remain `ready` / exhausted `needs_replan` | Up to the wave limit |
| **handoff** | The implementation is fine but a required output is absent | | — | `repairing` | Narrow fixer, `human_required` after the limit |
| **integration** | Merge conflict | | — | `integration_conflict` | replan or a human |
| **task definition** | A definition defect for which retry is meaningless | `policy_denied_at_runtime`, `ac_not_discriminating`, `no_op_detected` | — | `needs_replan` | None |
| **budget** | Budget exhausted | | `budget_exhausted` | `human_required` | None (stop) |

The three classes `handoff`, `integration`, and `task definition` **record no verdict.** They have only a state.

### The boundary between `blocked` and `error`

These are the two values most often confused. There is one criterion.

> **Is it resolved once a human fixes the environment?** → `blocked`
> **Does harness or adapter code have to be fixed?** → `error`

| Situation | Class |
|---|---|
| The `claude` CLI is not on PATH | `blocked` (installing it resolves it) |
| A placeholder in the adapter configuration could not be interpreted | `error` (a code defect or a misconfiguration) |
| `DATABASE_URL` is absent | `blocked` |
| A required field is absent in `AgentResult` | `error` (protocol violation) |
| The Docker daemon is off | `blocked` |
| A `require_approval` command was met during unattended execution | `blocked` (a human approving it resolves it) |
| The result of folding the journal differs from state | `error` |

The adapter `preflight`'s `kind` follows this boundary exactly. See 04.

---

## Crash Consistency

```
1. append the event to journal.jsonl
2. fsync
3. update state.json
```

This order is immutable. No matter where it dies, there is no loss.

| Point of death | Result |
|---|---|
| Before 1 | The same as if nothing had happened |
| Between 1 and 2 | The partially written last line is discarded on parse failure. That event becomes as if it never happened |
| Between 2 and 3 | The journal is ahead. On resume, replaying everything after `last_applied_seq` restores it |
| After 3 | Normal |

- If the journal's last line is broken, **only that line is discarded.** The preceding events are valid.
- If `state.json` itself is damaged it is reconstructed by folding the whole journal. state is a cache and can be thrown away at any time.

---

## Resume

```
harness run --resume <run-id>
```

**It must be idempotent.** Resuming with the same run-id any number of times must produce the same result.

| State at the time of resume | Handling |
|---|---|
| `done` (verdict `verified`) | **It is not re-run** |
| `pending`, `ready` | Normal scheduling |
| `precheck`, `running` | The agent's execution did not finish. Clean up the worktree and outbox and **start that attempt over from the beginning** |
| `executed`, `verifying`, `reviewing`, `repairing` | The agent's execution finished. If the worktree and outbox remain, **resume that attempt from verification** — the baseline is restored from `ac_baseline_executed` in the journal, so the agent is not called again. If they do not remain, that attempt from the beginning |
| `human_required`, `needs_replan`, `integration_conflict` | It is not resumed. After a human acts, a new run or an explicit resume |

A dead attempt restarts **under the same attempt number**. A crash does not exhaust the retry limit — `max_attempts` is the limit on adjudications that fell short of the goal, not a limit on how many times the process died. **The absence of `verdict_assigned` for that attempt is the marker that it did not finish.**

Even under parallel execution the rule is the same. Handling is determined by the task's state, and how many were running at once does not enter into the resume decision. A worktree that belongs to no run is cleaned up by `doctor`.

---

## `harness doctor`

It performs diagnosis and recovery. It is safe to execute.

| Check | Action |
|---|---|
| `state == fold(journal)` | On mismatch, reconstruct state from the journal |
| journal `seq` gaps or running backwards | Report. It does not auto-fix |
| Orphan worktree | Remove it if it belongs to no run |
| Stale lock | Check whether the pid is alive and release it if it is dead |
| Stray outbox | Clean up attempt directories left behind without being promoted |
| adapter `preflight` | Execute it on every registered adapter and report prerequisite / system defect separately **by the classification table above** |
| `.harness/` structure | Presence or absence of the required files |

`doctor` does not modify the journal. The journal is immutable.

---

## `human_required` Runbook

When a human has to intervene, what to look at and how to get back must be in the documentation. Without it, intervention becomes guesswork.

```
1. Grasp the situation
   harness status
   → check which task is human_required and for what reason

2. Check the evidence
   .harness/runs/<run-id>/tasks/<task-id>/verification.json
   .harness/runs/<run-id>/journal.jsonl  (filtered by that task_id)
   → check what the harness observed. The claim is for reference only.

3. Action by reason
```

| reason | What to check | Action | Return |
|---|---|---|---|
| prerequisite | The failed items of the `precondition_checked` event | Prepare the environment (install, login, env) | `harness run --resume <run-id>` |
| Unapproved command | The `command_policy_decision` event | Examine the command and add it to `.harness/approved_commands.yaml`, or modify the task | `--resume` |
| Repeated system defect | The `error` event's detail, transcript | Fix the harness or adapter | `--resume` after the fix |
| `handoff_missing` | The `handoff_rejected` / `handoff_missing` event | Re-examine whether `outputs.required` is justified. If the demand is excessive, demote it to `optional` | `--resume` after modifying the task |
| `needs_replan` | The reason of `verdict_assigned` | Modify the task definition (AC discriminating power, `allowed_paths`, commands) | A new run after `harness analyze` |
| `integration_conflict` | The list of conflicting files | Manual merge or splitting the task | `--resume` after it is resolved |
| `budget_exhausted` | The `budget_checkpoint` event | Raise the budget or reduce the scope | A new run |

---

## Termination on Partial Failure

Even when part of the DAG is blocked, **the run terminates normally.** It does not kill the process or give up on the rest.

- `blocked` blocks only that task and its downstream dependents.
- Branches of the DAG that are not blocked run to completion.
- The `run_finished` event and the end-of-run summary record what was blocked and why, what was completed, and what the open_debts are.

If one stage's failure aborts the whole run, the user has to start over from the beginning every time. That is worse than having a resume feature.
