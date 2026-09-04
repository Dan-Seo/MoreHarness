# 11 · Evaluation

This document is the **canonical location for metric definitions**.

> Principle 7 — improvement is measured.

If the harness cannot be proven to actually improve results, this framework has added nothing but complexity. eval is the apparatus for that proof.

---

## The two kinds of eval are not mixed

|  | regression eval | capability eval |
|---|---|---|
| What it asks | does the orchestration behave according to the convention | does the harness actually improve results |
| Adapter | `mock` | the real vendor adapter |
| Determinism | complete | nondeterminism → repeated runs plus variance reporting |
| Cost | 0 | real money |
| Location | required in CI | manual or periodic execution |
| Introduced | **M1** | **M7** |

Mixing them makes both unusable. Either CI becomes nondeterministic, or capability measurement becomes a re-confirmation of the mock's scenario.

---

## Fixture

```
evals/fixtures/<case>/
  seed/                   # the entire initial repository
  tasks/                  # optional. without it the harness builds plan/tasks from the spec in seed
  grader/
    hidden_ac.yaml        # the grading criteria
    expected.yaml         # the expected shape of the outputs
  meta.yaml               # difficulty, domain, expected duration
```

`seed/` is the entire initial repository. So `.harness/` is inside it, and so is `specs/<slug>/spec.yaml` (the file layout in 03). `.harness/` is where a fixture declares its own adapter and Command Policy.

### `grader/expected.yaml` — the grading criteria of the regression eval

What the regression eval asks is "did the orchestration behave according to the convention". So what is graded is not the code produced but **the final state the journal produced**.

```yaml
tasks:
  T-001: {verdict: verified, state: done}
  T-002: {verdict: blocked,  state: human_required}
open_debts: []              # the command list of the debts that must remain
human_required: [T-002]
```

- Keys not written are not graded. The fixture writes only what it asserts.
- A `verdict` of `null` is an assertion too — the case in 03 of "having only a state, with no verdict".

`hidden_ac.yaml` grades the final tree and is used by the capability eval. The regression eval runs on `mock`, so it does not grade the tree.

### The absolute rule of the hidden grader

> **The grader's ACs never enter the task `acceptance`, and never enter context assembly.**

The moment they enter, the agent optimizes for the grading criteria and the measurement becomes meaningless. This is not a convenience convention but the validity of the eval itself. When `eval/fixtures.py` loads a fixture it physically excludes `grader/` from the context sources.

---

## Arm

| arm | Content |
|---|---|
| `raw` | the full text of the spec injected at once, without the harness |
| `harness-lite` | the kernel only (the kernel/optional boundary in 02) |
| `harness-full` | everything |
| `ablation:<feature>` | one specific feature removed |

`raw` too is executed through a **thin measurement-only wrapper**. The wrapper does not adjudicate — what it leaves in the journal is `run_started`/`run_finished`, `agent_started`/`agent_finished`, and, if the fixture has `tasks/`, the post-hoc execution of the union of their ACs (`ac_post_executed` and the `command_policy_decision` it produces), and nothing else. There are no adjudication events. That is how every arm's metrics come out of the same journal schema.

### The assembly of an arm

| arm | Assembly |
|---|---|
| `raw` | The measurement wrapper. The prompt is the full text of `specs/*/spec.yaml` concatenated, and the workspace is the working tree of the materialized repository itself. The outbox is outside the repository. The post-hoc execution of the ACs also goes through Command Policy |
| `harness-lite` | The kernel only — sequential runner. No context assembly, no review, `max_parallel` forced to 1 |
| `harness-full` | The same assembly as `harness run` — context builder + review stage, and the parallel scheduler if `max_parallel > 1` |
| `ablation:<feature>` | `harness-full` with exactly one thing removed. `feature` ∈ {`context`, `review`, `parallel`} |

---

## Every arm is graded by the same hidden grader

```
raw   harness-lite   harness-full   ablation:<f>
  │         │              │              │
  └─────────┴──────┬───────┴──────────────┘
                   ▼
        the same hidden grader
                   ▼
            external_success
```

Comparing arms by the harness's ship gate does not hold, because `raw` has no ship gate to begin with. **The grader must live outside the harness.**

### `grader/hidden_ac.yaml` — the grading criteria of the capability eval

```yaml
acceptance:
  - cmd: ["pytest", "-q", "tests/hidden"]
  - cmd: ["python", "-c", "import app"]
    shell: false
```

- An item has the same shape as a task `acceptance` — an argv list is the default and `shell` must be declared.
- `{grader}` in an argv item is substituted with the absolute path of that fixture's `grader/`. It is there to reference
  material used only for grading without planting it in the tree. The substitution happens only in grader execution, and
  `grader/` is still absent from context assembly.
- The execution cwd is **the tree being graded**, and the timeout is the config `ac_timeout_s`.
- If all are green, that run is a grader success. `hidden_ac_pass_rate` is the number of green ACs / the total number of ACs.
- Grader commands also go through Command Policy — by the config of the materialized repository. If the policy blocks it, that AC is red and the reason is recorded in `eval.json`.
- The run journal is closed by `run_finished`, so **the grader does not write to the journal.** `eval.json` holds the grader's results and the policy adjudication.
- If `hidden_ac.yaml` is absent or `acceptance` is empty, the capability eval **refuses that fixture with an error without running it.** A measurement with no grading criteria only succeeds vacuously.

### The tree being graded

The cwd where the grader runs is **the tree the user ends up with after the run finishes**.

| Execution | What is graded |
|---|---|
| `raw` arm | the very working tree the agent worked in |
| harness arm | if an integration branch exists, a detached checkout at its tip (the same way as converge); otherwise the repository working tree |

The criterion is not the declared profile but **the existence of the integration branch** — even with per-task profile overrides mixed in, what is graded is the tree ship would merge.

### The denominator of `escape_rate` / `false_block_rate`

- The unit of measurement is **one run** (fixture × arm × repeat), and it is defined only for harness arms.
- "adjudicated verified" = the final verdict of every task in the run is `verified`.
- "adjudicated rejected/blocked" = at least one of the final verdicts is `rejected` or `blocked`.
- `escape_rate` = the rate of grader failure among verified runs. `false_block_rate` = the rate of grader success among rejected/blocked runs.
- If the denominator is 0 the metric is `null` and is shown as `n/a` in the report.

---

## Metrics — canonical

### External outcome metrics — the core of arm comparison

The criterion is an oracle outside the harness.

| Metric | Definition |
|---|---|
| `grader_success_rate` | the rate of fixtures that succeeded by the hidden grader's criterion. **Arm comparison uses this value only** |
| `hidden_ac_pass_rate` | the pass rate in units of a hidden AC |

### Harness calibration metrics — how accurate the harness's judgment was

| Metric | Definition |
|---|---|
| `escape_rate` | the rate at which the harness adjudicated `verified` but the hidden grader saw a failure |
| `false_block_rate` | the rate at which the harness adjudicated `rejected`/`blocked` but grading the final tree with the hidden grader is a success |

**These two justify the design or refute it.**

- A high `escape_rate` means the ACs and the review have no discriminating power. It is the quantification of the "largest remaining risk" 06 admits.
- A high `false_block_rate` means the harness is blocking correct work. Differential adjudication and the review tiers must be re-examined.

### Internal metrics

| Metric | Definition |
|---|---|
| `ship_gate_pass_rate` | the rate of passing the ship gate. **It is meaningful only for harness arms and is not used for arm comparison** |

### Cost and speed metrics

| Metric | Source event |
|---|---|
| `first_pass_rate` | `verdict_assigned` (the rate of being verified at attempt 1) |
| `retry_count` | the maximum attempt of `verdict_assigned` |
| `review_waves` | `review_finding`, `fixer_dispatched` |
| `wall_time_s` | `run_started`, `run_finished` |
| `agent_time_s` | the sum of `agent_finished.duration_s` |
| `tokens_in` / `tokens_out` | `agent_finished.usage` |
| `cost_usd` | `agent_finished.usage` — `null` when the vendor does not report it. **It does not estimate** |
| `human_interventions` | the number of times it went to `human_required` |
| `convergence` | coverage % at ship time, the number of drifts, the number of waves to convergence |
| `context_tokens` | `total_tokens` in `context.manifest.json` |

**All of them are projections of the journal, and there is no separate instrumentation code.** No counters are planted in the code for a metric. If a new metric is needed, first look at whether the event it needs exists.

`cost_usd` is `null` when the vendor does not report it. No token-price estimation model is built. A `null` metric is shown as `n/a` in the report and excluded from averages.

---

## Execution and reporting

```
harness eval run --fixtures evals/capability --arms raw,harness-full --repeat 3
```

- `--fixtures <dir>` looks for `<dir>/<case>/seed/`, and failing that for `<dir>/fixtures/<case>/seed/`.
- This repository puts regression fixtures in `evals/fixtures/` and capability fixtures in `evals/capability/`. The capability eval refuses a fixture whose `hidden_ac.yaml` is empty, so the two are not mixed in one directory.
- The outputs are `eval-report.md` and `eval.json`, written to `--out` (default: the `--fixtures` directory).
- The run repositories remain in a working directory under the system temp, and `eval.json` records that path. Digging into a failure needs the journal.

### Reporting rules for the capability eval

- **Report the median together with the variance.** Rate metrics (`grader_success_rate` and the like) are aggregated as a rate in units of a run, with the per-fixture breakdown listed. Continuous metrics (`wall_time_s`, `agent_time_s`, tokens, `cost_usd`, `context_tokens`) report **the median and [min, max]**. `null` values are excluded, and if all are `null` it is `n/a`.
- **Presenting a single-run figure as the basis for improvement is prohibited.** LLM execution is nondeterministic, so one good result is not information.
- The default for `--repeat` is 3, and the report shows a warning on a result run fewer times than that.
- Each arm's failure cases are listed per fixture. Showing only the aggregate leaves no way to know where and why it failed.

---

## Position in the architecture

`eval` is **outside the kernel**. It follows the dependency direction in 02.

```
exec/* · context/*  ←  eval/*  ←  cli
```

- **`cli` calls `eval`.** `eval` does not import `cli`.
- The lower-level APIs `eval` depends on are only `store` (reading the journal), `exec/runner` (execution), `adapters/registry` (selecting the adapter per arm), `spec` (the skeleton for a fixture with no `tasks/`), and `models`.
- The only module that imports `eval` is `cli`. Circular dependency is forbidden.
- Commands an eval fixture executes also go through Command Policy. See 06.

---

## External benchmarks

Connecting a large external benchmark is isolated as **the problem of building one more fixture adapter**. With one layer that converts the external benchmark's case format into the `evals/fixtures/<case>/` structure, the rest works as is. Neither the kernel nor the metric definitions change.

```
harness eval import --benchmark swebench --instances <jsonl> --repos <dir> --out <dir>
```

`--instances` is a JSON Lines file of SWE-bench instances, and `--repos` is **the directory holding the local checkouts** of the repositories those instances point to (`<owner>__<name>` or `<owner>/<name>`). **The converter uses no network** — a human fetches beforehand and the converter only converts.

### Field correspondence

| SWE-bench field | fixture |
|---|---|
| `instance_id` | the case directory name |
| `repo` + `base_commit` | `seed/` — only that commit's tree is taken out of the local checkout. `.git` does not come along |
| `problem_statement` | R-001 of `seed/specs/<instance_id>/spec.yaml` |
| `test_patch` | `grader/test_patch.diff` — **it is not put in `seed/`** |
| `FAIL_TO_PASS` · `PASS_TO_PASS` | the acceptance in `grader/hidden_ac.yaml` |
| `version` · `environment_setup_commit` · `created_at` | `meta.yaml` |

That the tests are hidden is the essence of this benchmark, and that is the same rule as "The absolute rule of the hidden grader" above. So the test patch lives in `grader/` and is applied only at grading time.

```yaml
# grader/hidden_ac.yaml
acceptance:
  - cmd: ["git", "apply", "{grader}/test_patch.diff"]
  - cmd: ["python", "-m", "pytest", "-q", "<FAIL_TO_PASS ...>"]
  - cmd: ["python", "-m", "pytest", "-q", "<PASS_TO_PASS ...>"]
```

- If the first item is red, grading did not stand up at all, so red is correct.
- FAIL_TO_PASS and PASS_TO_PASS are each bundled into one command. That is how `grader_success_rate` comes out the same as that benchmark's own definition of success (all F2P pass ∧ no P2P regression).
- Test identifiers are in pytest node id form. For a repository that uses its own runner, a human edits the generated `hidden_ac.yaml`. **The converter is a converter, not a benchmark executor** — preparing the Python environment is not the converter's job.

### The task that gets created

The converter creates exactly one requirement (R-001) and **does not write `tasks/`.** By the rule in "Fixture" above, the harness builds the skeleton from the spec, and that skeleton has **no visible AC.** Hiding the grading criteria is the essence of this benchmark, so the evidence the harness holds is the diff and the review, and nothing else. This is not a defect of the converter but the condition the harness is actually placed under in this benchmark, and `escape_rate` exposes it.

`grader/expected.yaml` is not created — an external benchmark fixture is for the capability eval, and the regression eval runs on `mock`.

### Skipping and idempotence

- If the local checkout is absent, or `base_commit` is not in that checkout, **only that instance is skipped and the reason is reported.** One missing item does not fail the whole conversion.
- If `FAIL_TO_PASS` and `PASS_TO_PASS` are both empty it is skipped. A fixture with no grading criteria is not created.
- Running again into the same `--out` gives the same result. An existing case directory is rewritten wholesale.
- The converter creates `seed/.harness/` with defaults. The adapter and Command Policy are what a fixture declares, so a human edits them before use.
