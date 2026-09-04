# 01 · Terms

This document is the canonical definition of the terms. Other documents do not call the same concept by a different name.

## The Most Important Distinction

Four things get lumped together often, and the moment they are, the harness ends up using the agent's self-report as adjudication.

```
Claim      what the agent said: "this is what I did"         — narrative. a hint. optional.
Handoff    structured data the agent passes to the next task — a contract. a separate file.
Evidence   what the harness observed itself                  — fact. the only basis for adjudication.
Verdict    the conclusion the harness drew from evidence     — one of five values.
```

**Claim is not Evidence.** Even if the claim is absent, malformed, or false, adjudication proceeds identically.

---

## Pipeline Outputs

**Intent** — the purpose a human stated in natural language. The input to the pipeline.

**Spec** — Intent organized into a set of verifiable requirements. `specs/<slug>/spec.yaml`. Unresolved questions remain as `[NEEDS CLARIFICATION]` markers, and `analyze` blocks them.

**Requirement (`R-###`)** — the atomic unit of the spec. **The axis of convergence traceability.** Every task declares which R-### it satisfies through `satisfies`, and `converge` builds an R-### → task → verdict → evidence matrix.

**Plan** — the decision about the order and structure in which the requirements are implemented. The basis for the Task DAG.

**Task** — the unit of execution. `tasks/T-###.task.yaml`. The contract is defined in 03.

**Task DAG** — the directed acyclic graph formed by `depends_on` relations between tasks. The basis for what can run in parallel and the object of `analyze`'s check.

---

## Units of Execution

**Run** — one execution of `harness run`. Identified by `run_id`, and it holds every record under `.harness/runs/<run-id>/`.

**Attempt** — one attempt at executing the agent for one task. Retrying after `rejected` or `error` raises the attempt number. **A verdict is assigned per attempt.**

**Workspace** — the agent's cwd. Under the `worktree` profile it is a git worktree created outside the repository. See 05.

**Outbox** — the directory the agent writes its result artifacts (claim, handoff) into. It is outside the workspace and outside `.harness/`. A new one is created per attempt. See 04.

---

## On Adjudication

**Claim** — the agent's self-report. **It is optional and a hint.** Adjudication proceeds whether it is absent or wrong. No verdict is decided by the claim alone. A claim's `blocked_hint` is not a conclusion but a hypothesis for the harness to check, and if the harness cannot corroborate it with a probe, it is not accepted.

**Handoff** — structured data the next task will use. It is a **separate artifact** from the claim. Narrative and data differ in both consumer and lifetime, so the files are separated. If one is broken, the other survives.

**Evidence** — observations the harness itself produced. Precondition probe results, AC execution results, git diff, path adjudication, Command Policy adjudication, review findings. **The only basis for adjudication.**

**Acceptance Criteria (AC)** — the commands a task must satisfy, which the harness itself runs. It is not the agent that runs them. When `expect_fail_before` is attached, a red→green proof is required. See 06.

**Verdict** — the result of adjudication. **`verified | rejected | blocked | error | budget_exhausted`** — exactly these five. The canonical definition is 03.

**State** — the task's position in the workflow. It is a **different axis** from verdict. Values such as `repairing`, `needs_replan`, `human_required` are states, not verdicts. The canonical definition is 03.

**Finding** — a structured finding a reviewer raised. `{severity, rule, file, line, message, blocking}`. `blocking` is computed by a deterministic rule.

**Debt** — a problem that is no task's responsibility but remains unresolved. Typically an AC that was already failing before the task ran. **It does not block task adjudication; it blocks ship.** The `open_debts` ledger of 08 manages it.

---

## Records

**Event** — an immutable record appended to the journal. **The canonical truth and the source of every metric.** A correction is made by a new event, not by deletion.

**Journal** — `journal.jsonl`. The whole event log of a run. A single writer.

**State projection** — `state.json`. A derived snapshot made by folding the journal. It can be reconstructed at any time. The journal is canonical and state is a cache.

---

## Policy and Isolation

**Execution profile** — `safe | worktree | container | unsafe`. What each profile guarantees and does not guarantee — 05 is canonical.

**Command Policy** — the approval procedure a command must go through before the harness executes it. `allow | deny | require_approval`, default fail-closed. `allow` does not mean "this command is inherently safe"; it means **"a command class approved for automatic execution in this project."** The canonical definition is 06, and the security meaning is 09.

**effective_risk** — `max(declared_risk, path_floor, diff_floor)`. It does not take the task's declared risk at face value; it raises the floor from the changed paths and the diff size. It determines the review tier. See 06.

**Provenance / trust** — the provenance of each compartment of the context and the trust level that follows from it. Trust is determined **by provenance alone**, not by layer number. The canonical definition is 07.

---

## Learning and Measurement

**Knowledge card** — reusable knowledge promoted from repeated observation. `.harness/knowledge/K-###.yaml`. **It cannot override the constitution, and it cannot become acceptance criteria.**

**Constitution** — `.harness/constitution.md`. The rules the project never breaks. It lives in the harness control-plane and is always trusted.

**Fixture** — the unit case of an eval. It has an initial repository, a spec, and a hidden grader. See 11.

**Arm** — the execution condition compared in an eval. `raw`, `harness-lite`, `harness-full`, `ablation:<feature>`.

**Hidden grader** — the grading criteria hidden in the fixture. **It never enters either the task's acceptance or the agent's context.** It is an external oracle that grades every arm by the same criteria.
