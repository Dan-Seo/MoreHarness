# 08 · Convergence and Learn

This document holds the **canonical definition of the ship gate and the debt ledger**.

There are two gates. One stands before implementation, one after.

```
tasks  →  analyze  →  run  →  converge  →  ship
          (up-front)          (post-hoc)
```

---

## Authoring stages — spec · clarify · plan · tasks

The outputs of these stages are filled in by a human (or by an agent a human directed). What the harness does is **make the shape (scaffold) and check consistency (analyze)**. The harness does not produce the quality of the content.

- `harness spec "<intent>"` — creates `specs/<slug>/spec.yaml` from a template. The slug is made from the intent or given with `--slug`. It contains an R-### skeleton and `[NEEDS CLARIFICATION]` items.
- `harness clarify` — shows the remaining `[NEEDS CLARIFICATION]`. On a TTY it takes the answers one at a time, moves them into `clarifications:`, and deletes the item. As long as any remain, the exit code is not 0.
- `harness plan` — creates `specs/<slug>/plan.md` from a template for each spec.
- `harness tasks` — creates a task skeleton for every R-### that no task satisfies. The harness stamps `spec_hash` at this point (03). acceptance is left empty — analyze blocks until it is filled in.

Existing files are not overwritten.

---

## `harness analyze` — the pre-implementation gate

**It does not look at the code.** It looks only at the consistency of the spec, plan, and task definitions.

### Checks

**Requirement traceability**
- Does `satisfies` point to an R-### that actually exists
- Is every R-### assigned to at least one task
- Is there an orphan task that satisfies no R-###

**DAG**
- Is there a cycle
- Does anything `depends_on` a task that does not exist
- When `max_parallel > 1`, is there an `allowed_paths` conflict between tasks that can execute concurrently

**Acceptance Criteria**
- Does every task have an AC
- Is the AC in an executable form (an argv list, an executable file that exists)
- **Does the AC have discriminating power** — this is the most important check. It **reviews the AC itself**, not the code. It flags a task whose only ACs catch nothing but regressions without `expect_fail_before`, and an AC that passes no matter what is done

**Up-front Command Policy detection**
- It applies Command Policy to every AC command and to every `kind: command` precondition
- A `deny` match is **reported as a failure and blocks `run`**
- A `require_approval` match is reported so that a human approves it in advance or fixes the task

**outputs**
- Is a harness-produced field (`changed_files` and the like) wrongly named in `outputs.required`
- Does a downstream task actually use a field declared `required`

**Drift and the unresolved**
- Does `[NEEDS CLARIFICATION]` remain
- Does the task's `spec_hash` match the current spec

### Failure and warning

analyze's issues come in two grades.

**Failure — blocks `run`**

- `satisfies` points to an R-### that does not exist
- There is an unassigned R-###
- The DAG has a cycle, or depends on a task that does not exist
- An `implementation` task has no AC
- An AC is not an argv list, or its executable file cannot be found
- A Command Policy `deny` match
- `outputs.required` contains a harness-produced field
- `[NEEDS CLARIFICATION]` remains (anywhere in the spec text)
- `spec_hash` mismatch (drift)

**Warning — reported but not blocking**

- An `implementation` task whose only ACs are without `expect_fail_before` (discriminating power in doubt)
- A Command Policy `require_approval` match — a chance for a human to approve it in advance
- An orphan task that satisfies no R-###
- `required` is declared but there is no downstream to consume it
- There is no `spec_hash`, so drift cannot be detected
- `max_parallel > 1` and the `allowed_paths` of tasks that could run in parallel overlap — execution is safe (they are serialized, 05). Only parallelism is lost

Among the requirement traceability checks, "unassigned R" and "orphan task" mean something only when `specs/` exists. In a repository with tasks but no spec it passes vacuously.

### Output and gate

- `.harness/analyze-report.md` — the report for a human
- `.harness/analyze.json` — `{ok, fingerprint, generated_at}`. The fingerprint is a hash of the contents of the `specs/` and `tasks/` files.

`harness run` uses `analyze.json` as a gate **when it is present** — `ok: false` blocks, and if the fingerprint differs from the current files, either way it blocks with "run analyze again". If `analyze.json` is missing, this repository does not use the gate — the run proceeds, because the kernel must work without analyze (the optional boundary of 02).

---

## `harness converge` — the post-implementation gate

**"Implementation is finished" ≠ "every requirement is implemented".**

### Coverage matrix

```
R-###  →  task  →  verdict  →  evidence
```

| State | Meaning |
|---|---|
| covered | A task that satisfies the requirement exists and its verdict is `verified` |
| uncovered | No task `satisfies` this R-### |
| unverified | A task exists but did not reach `verified` |
| partial | Of several tasks, only some are `verified` |

### Other checks

- **Drift** — the task's `spec_hash` and the hash of the current spec differ. It means the spec changed during implementation.
- **Orphan diff** — the integration branch holds changes that the `allowed_paths` of verified tasks do not explain. A task that does not declare `allowed_paths` has no restriction, so it explains every change (05).
- **Executing the union of all ACs** — it checks the integration branch out into a harness-owned temporary worktree and executes every task's ACs and the `health_commands` from config. They go through Command Policy. A red that corresponds to an open debt is reported as a ledger entry, and **every other red is a failure.**
- **open_debts** — the ledger is carried in the report. **Debt is not a failure condition of converge but a condition of ship.** If converge punishes again what differential adjudication exempted, the separation of the two levels collapses.

The conditions under which converge fails — there is an uncovered/unverified/partial · there is drift · there is an orphan diff · there is a red that does not correspond to a debt.

### Output

- `.harness/coverage.md` — the coverage matrix for a human
- `.harness/converge.json` — `{ok, run_id, integration, coverage, ...}`. ship reads this.

### The config keys 08 owns

```yaml
health_commands: []           # the list of argv lists converge executes on top of the AC union
```

---

## The debt ledger

Task adjudication is **differential** and the ship gate is **absolute**. What joins the two levels is the `open_debts` ledger.

```
a pre-existing failure is found at baseline
   → debt_opened { debt_id, cmd, origin_task }
   → it does not affect task adjudication (06 differential adjudication table)
   → it remains in open_debts[]

if it later turns green in some task's post
   → debt_closed { debt_id, closed_by }
```

- **Debt does not block a task.** Because a correct task must not be rejected by an unrelated pre-existing failure.
- **Debt blocks ship.** Because a broken state must not be released.
- `harness status` always exposes open_debts. They do not accumulate quietly.

---

## The ship gate — canonical

There are four conditions under which `harness ship` allows a pass. **All of them must be satisfied.**

```
1. every R-### is covered
2. the final verdict of every related task is verified
3. no drift (spec_hash matches)
4. open_debts is empty
```

The only exception is **a recorded human waiver**. It is read from `.harness/waivers.yaml`, and every time ship accepts one it is recorded in the journal as a `debt_waived` event (03).

```yaml
# .harness/waivers.yaml
waivers:
  - debt_id: D-002
    approver: "emdhks09@gmail.com"
    reason: "Legacy e2e suite. Tracked in the separate ticket PROJ-412."
    approved_at: "2026-08-27T18:00:00+09:00"
```

There is no path by which a broken state ships without a waiver.

### The ship procedure

```
1. Check that converge.json is for this run's **current** integration tip and is ok.
   Otherwise fail with "run converge first".
2. Subtract from open_debts what a waiver exempted; if anything remains, fail.
3. On a pass, merge the integration branch into the user's current branch. Because the
   user's branch did not move during the run (05) it is usually a fast-forward. On a
   conflict, abort and report a failure. A safe profile run has no integration branch —
   the changes are already in the main worktree, so there is no merge.
4. Write .harness/ship-report.md.
```

---

## Knowledge cards

```yaml
# .harness/knowledge/K-004.yaml
id: K-004
kind: pattern              # pattern | pitfall | convention
rule: null                 # the source rule of a card learn proposed. A human-written card is null.
scope: "src/api/**"
claim: "route handlers in this repository delegate authentication to middleware and do not check it themselves"
evidence:
  - run: run-20260820-1102
    task: T-003
  - run: run-20260825-0930
    task: T-011
status: candidate          # candidate | promoted | retired
uses: 0
```

### Promotion and retirement

- **Only a human promotes.** Being observed repeatedly across different runs only creates eligibility for `candidate`,
  and that alone does not make it `promoted`.
- **Only a human retires.** When it goes long unused or a contradicting observation appears, the harness reports it,
  and changing it to `retired` is a human command.
- `uses` is the number of times it was actually included in the context. Knowledge that is not used is not knowledge.

### Absolute rules

- **Knowledge cannot override the constitution.**
- **Knowledge cannot become acceptance criteria.** Promoting a tendency drawn from observations into a criterion for adjudication makes the harness verify its own bias.
- A knowledge card is **untrusted** in the context. See 07.

### `harness learn`

It reads the journal of a finished run to propose candidates, updates the cards' `uses`, and reports retirement targets.
**Only a human command changes status.**

| Command | What it does |
|---|---|
| `harness learn` | propose candidates · update `uses` · report retirement targets |
| `harness learn promote K-004` | record `status: promoted` |
| `harness learn retire K-004` | record `status: retired` |

**The source of a candidate is a repeated review finding.** When the same `rule` is observed in different runs
`knowledge.candidate_after` times or more, a `candidate` card is made from that rule.

- **Repetition within the same run counts once.** Several waves and several reviewers in one run are not independent observations.
- **`rule` is the key that joins a card to an observation.** If a card with the same `rule` already exists, no new one is made and
  `{run, task}` is appended to `evidence`.
  A `retired` card never becomes a candidate again — the harness does not overturn a judgment a human made.
- `scope` is the common directory of the files that rule flagged, with `/**` appended.
- `claim` is left as `[NEEDS CLAIM]`. **learn only gathers observations; it does not invent sentences.**
- `kind` is `pitfall`. A repeated finding is not a pattern but a pitfall.

**`uses` is counted from the L7 section records in `context.manifest.json`.** A section dropped for budget is not
counted — only what actually entered the prompt is a use. No counter is planted in the execution path (11).

The criterion for the retirement report is `knowledge.retire_after_unused_runs`. If a `promoted` card has not been
included in the context even once for that many runs or more, it is reported.

### The config keys 08 owns — knowledge

```yaml
knowledge:
  candidate_after: 2            # a candidate when the same rule repeats this many times across different runs
  retire_after_unused_runs: 10  # report retirement when a promoted card goes unused for this many runs
```
