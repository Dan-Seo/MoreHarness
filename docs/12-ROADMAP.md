# 12 · Roadmap

Each capability appears in **exactly one milestone**. If a feature spans two milestones, the scope was cut wrong.

Milestones proceed in order. Do not pull a later milestone's features forward.

---

## M0 — Kernel Contract

**What gets built**

`models`, `events`, `schemas/`, `config`, `store` (journal append + state projection + reconstruction), the `git` wrapper, `adapters/{base, registry, mock}`, and `cli`'s `init` · `status` · `doctor`.

**Completion criteria**

- The `state == fold(journal)` reconstruction test passes
- After killing the process at an arbitrary point, state is restored from the journal
- In a repository created by `harness init`, `doctor` passes with no findings
- **It works with neither an LLM nor a network**

---

## M1 — Execution and Adjudication

**What gets built**

`dag`, `exec/runner` (sequential, `max_parallel=1`), the outbox convention (claim + handoff), `probes`, **`policy` (Command Policy)**, `exec/verify` (baseline/post AC + per-`kind` diff expectations + differential adjudication + debt), **`exec/handoff`** (the required/optional gate + `repairing`), blocked/error classification, the exit code rule, `adapters/generic_cli`, `adapters/conformance`, `cli`'s `run`, and the regression eval runner.

**Completion criteria**

A 3-task DAG runs to completion on both `mock` and `generic_cli`. And the following five are **proven by test.**

1. **Even when the claim is broken, `verified` is still reachable**
2. **A missing required handoff does not throw the implementation away** (only the handoff is recovered, via `repairing`)
3. **A `deny` command is not executed**
4. **An unrelated pre-existing failure does not make the task `rejected`**
5. **Even with a non-zero exit, if the evidence passes it is `verified`**

These five are the whole of v2's core contract. If they are not proven at M1, the later milestones are meaningless.

---

## M2 — Isolation and Resume

**What gets built**

The `worktree` profile (**placed outside the repository**), `exec/workspace`, path scope enforcement, the integration branch, `run --resume`, and `doctor` recovery.

**Completion criteria**

- A path violation automatically becomes `rejected`
- After forced termination, resume restores from the journal and is **idempotent**

---

## M3 — Parallelism

**What gets built**

`exec/scheduler` — `max_parallel: N`, ready-set computation, `allowed_paths` conflict serialization, and the merge queue.

**Completion criteria**

- Tasks whose paths do not overlap execute concurrently
- Overlapping tasks are serialized
- **There is still exactly one journal writer**

---

## M4 — Context Selection

**What gets built**

`context/{builder, repomap, slicing, budget}`, `context.manifest.json`, provenance-based trust marking, and the prompt compartment convention.

**Completion criteria**

Prompt tokens decrease **measurably** against full injection. The decrease is reported by the `context_tokens` metric.

---

## M5 — Risk and Review

**What gets built**

`risk` (effective_risk), review tiers, bounded wave, fixer, and the budget cap.

**Completion criteria**

When a task declared `trivial` changes a sensitive path, **the tier is automatically escalated and it is reviewed.** It can be confirmed by the `risk_escalated` event.

---

## M6 — Spec Pipeline

**What gets built**

`spec`/`clarify`/`plan`/`tasks`, `analyze` (including up-front Command Policy detection), `converge`, the ship gate, and the debt ledger.

**Completion criteria**

The following three **each** block a gate.

- An uncovered requirement → `converge` fails
- Unresolved debt → `ship` fails
- A task containing a `deny` command → `analyze` fails

---

## M7 — Vendor Adapters and Capability Evaluation

**What gets built**

`adapters/{claude_cli, codex_cli}` (usage reporting), the capability eval — fixtures, arms, hidden grader, repeated runs and variance reporting.

**Completion criteria**

It produces a `raw` vs `harness-full` comparison report with the same hidden grader. `escape_rate` and `false_block_rate` are measured.

**This milestone verifies the framework's reason to exist.** If the result shows no improvement, revisit the design.

---

## M8 — The Optional Layer

**What gets built**

`learn`, the `container` profile, cross-model adversarial review, and the external benchmark fixture adapter.

**Completion criteria**

**Prove by test that the kernel works with the entire optional layer removed.** This test enforces that the kernel/optional boundary of 02 is real.

---

## M9 — TDD Enforcement Mode

**What gets built**

`development.mode` (03) and `exec/tdd` — the test-author dispatch, the red gate, the
implementation dispatch, phase scope adjudication, the `tdd_phase_completed` event, and
resume after the red gate.

**Completion criteria**

- If the test-author phase touches an implementation path, **the harness detects it from the diff** and it is rejected. It is stopped by evidence, not by a prompt instruction.
- If an AC is already green at the red gate, **the implementation is not dispatched.**
- If the implementation phase modifies or deletes a test, it is detected. **The same diff observes it even if it is hidden in a commit.**
- `--resume` on a run that crashed after the red gate does not run the test-author again.
- The behavior of an existing task that does not declare `development` is unchanged.

---

## Rehearsal checklist for implementation sessions

A session that starts a milestone must be able to begin **by reading only this document set**. If the answer to the following questions is not in the documents, that is a gap in the design, not something the implementer gets to decide.

**Before starting M0**
- What are the kinds of journal event and their payloads? → 03
- What is the `seq` assignment rule and the event `id` form? → 03
- What is the `state == fold(journal)` reconstruction procedure and the crash recovery order? → 03, 10
- What is the full set of conformance items the `mock` adapter must satisfy? → 04
- What control-plane items must `init` create? → the file layout in 03

**Before starting M1**
- What is the cwd, timeout, and exit code interpretation for AC baseline/post, and the green/red classification rule? → 06
- How does adjudication differ when the claim and the handoff are each absent or invalid? → 06
- What is Command Policy's adjudication order, the storage format for approvals, and the behavior under unattended execution? → 06
- What is the minimum evidence required for a `blocked` adjudication? Where is the boundary with `error`? → 06, 10
- What are the conditions for the `rejected → ready` and `repairing → verified` transitions, and the adjudication of attempt exhaustion? → 03, 06
- What is the procedure for writing one regression eval fixture from scratch? → 11

**From M2 onward**
- What are the exact paths and lifecycle of the worktree and the outbox? → 05
- What is the path scope glob matching rule? → 05
- What is the handling per state on resume? → 10

---

## Document maintenance rules

- The canonical definition of a concept lives in **exactly one document**. Other documents reference it and never restate it. The canonical location is in the document map of 00.
- When a contract changes, fix the canonical document and **delete** the sentences it supersedes. Do not put the old description and the new one side by side.
- **Do not leave revision history in the body of a document.** A document describes only the final contract. git holds the history.
