# 00 · Overview

> **Agents propose. Harness verifies. Evidence decides.**

## What this framework does

It puts a coding agent to work, and **the harness itself checks whether the result is real.**

The agent writes code and reports what it did. The harness does not use that report as a basis for adjudication. Instead it runs the acceptance criteria itself, reads the git diff itself, and adjudicates the changed paths itself. The only basis for adjudication is the observations the harness itself produced.

## Why this is needed

There is a path agent orchestrators commonly take.

> The control loop reads the truth from a state file the agent wrote itself.

It calls the agent, reads back the JSON in which the agent wrote `status: completed`, and takes that as the adjudication. Do this once and everything else follows. There is no reason left to run verification, no reason to look at the diff, and a non-zero exit code leaves nothing but a warning. Review runs only when a human remembers it, retries repeat the same failure, and there is no way to measure what improved.

v2 starts by inverting that one point. **The agent does not hold adjudication authority.**

| Common pattern | v2's response | Document |
|---|---|---|
| The agent self-declares completion | The harness adjudicates from independent evidence | 06 |
| Full injection of the documents on every call | Layered context + budget | 07 |
| A specific vendor CLI hardcoded into the executor | Adapter protocol + conformance | 04 |
| Permission-bypass flags always on, execution in the main worktree | Execution profile + a worktree outside the repository | 05 |
| Commands written by the agent or the planner executed unverified | Command Policy | 06, 09 |
| Only linear execution is possible | Task DAG + parallel scheduler | 03, 05 |
| No independent review in the execution path | effective_risk tier + bounded wave | 06 |
| Handover between stages is one line of prose | A structured handoff contract | 03 |
| Spec↔implementation agreement is never checked | R-### traceability + `converge` | 08 |
| One stage's blocked aborts the entire run | task-local blocked | 03 |
| Resume means "hand-editing the state JSON" | journal replay | 10 |
| No way to measure improvement | eval framework | 11 |

## Pipeline

```
Intent → Spec → Plan → Task DAG → Analyze (up-front gate)
      → Context Selection → Isolated Execution
      → Deterministic Verification → Independent Review
      → Converge (post-hoc gate) → Ship → Learn
                                         ↘ Evaluate (instrumented from the journal)
```

Every arrow is owned by the harness. The agent operates only inside `Isolated Execution`, and has no authority to pass its own results to the next stage.

## Design principles and enforcement points

A principle is not a declaration; it is enforced at a specific point in the code. A principle with no enforcement point is not a principle.

| # | Principle | Enforcement point | Document |
|---|---|---|---|
| 1 | The harness adjudicates the truth | `exec/verify.py`. Agent artifacts are isolated in the outbox and are promoted only after the harness reads and verifies them | 03, 04, 06 |
| 2 | fresh context over a long conversation | Process separation per task. State carries over as artifacts, not as a conversation | 03, 04 |
| 3 | Context is selection, not a dump | `context/builder.py` + token budget + `context.manifest.json` | 07 |
| 4 | Review is independent and finite | effective_risk tier + `MAX_REVIEW_WAVES` + budget cap | 06 |
| 5 | The harness enforces isolation | `exec/workspace.py` + `policy.py` + post-hoc path adjudication | 05, 06, 09 |
| 6 | A simple kernel, optional advanced features | Kernel/optional separation. Proven by test that the kernel works with the entire optional layer removed | 02, 12 |
| 7 | Improvement is measured | `harness eval`. Every metric is a projection of the journal, and there is no separate instrumentation code | 11 |

## CLI map

```
harness init                  Create .harness/ in the repository
harness spec <intent>         Intent → Spec (assign R-###)
harness clarify               Resolve [NEEDS CLARIFICATION]
harness plan                  Spec → Plan
harness tasks                 Plan → Task DAG
harness analyze               Pre-implementation gate. Blocks the run on failure
harness run [--resume <id>]   Execute the DAG
harness converge              Post-implementation gate. Coverage · drift · debt
harness ship                  Integration and release gate
harness learn                 knowledge card promotion / retirement
harness eval run              Regression and capability eval
harness eval import           External benchmark → fixture conversion
harness status                The current run's state, open_debts, human_required
harness doctor                Consistency check and recovery
```

## Document map

A concept's **canonical definition is in exactly one document**. Other documents reference that place and do not restate it.

| Document | Role | What this document is canonical for |
|---|---|---|
| `00-OVERVIEW.md` | Goals · pipeline · CLI · principle enforcement points | The principle traceability table |
| `01-CONCEPTS.md` | Terms | Term definitions |
| `02-ARCHITECTURE.md` | Module boundaries · dependency direction · kernel/optional | Dependency direction |
| `03-DATA-MODEL.md` | Schema · state machine · event log · file layout | **verdict · state definitions**, the event list |
| `04-AGENT-ADAPTER.md` | Adapter protocol · `generic_cli` · conformance | The adapter contract |
| `05-EXECUTION-ISOLATION.md` | Execution profiles · workspace · parallelism | **Profile guarantees / non-guarantees** |
| `06-VERIFICATION-REVIEW.md` | AC lifecycle · adjudication · review | **The adjudication algorithm, Command Policy** |
| `07-CONTEXT-SELECTION.md` | Context assembly · budget | **context provenance / trust** |
| `08-CONVERGENCE-LEARN.md` | `analyze` / `converge` / ship / knowledge | The ship gate, the debt ledger |
| `09-SECURITY.md` | Trust boundary · threat model · non-guarantees | The threat model |
| `10-FAILURE-RECOVERY.md` | Failure handling · crash · resume | **The failure classification table** |
| `11-EVALUATION.md` | Regression / capability eval | **Metric definitions** |
| `12-ROADMAP.md` | M0–M8 | Milestone scope |

## Constraints

- Language: Python. The dependencies are **stdlib + `PyYAML` + `jsonschema`** and nothing else.
- No DB, server, or daemon in the repository. State is files.
- The kernel has no web UI.

## Explicit Non-Goals

A custom DSL, distributed execution, an estimation model for cost the vendor does not report, a backward-compatibility layer, a large policy engine (a config-driven rule list plus a fail-closed default is all there is), derived verdicts such as `verified_with_warning`.

The `container` profile, a tree-sitter-based repo map, cross-model review, and external benchmark integration are **optional** and do not belong to the kernel.
