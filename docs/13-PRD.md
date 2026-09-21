# 13 · Product Requirements

> **Who forks this, what a fork must do, and what makes it worth forking.**

The thirteen design documents are engineering contracts. They define the pipeline, the
adjudication algorithm and the adapter protocol. None of them says who the harness is
for, what a fork has to do before its first verified run, or what has to be true for the
product to be usable rather than merely implemented. That is this document.

This is a requirement, not a contract. It constrains no code path and adds no verdict.

## What this document is canonical for

Three things, and nothing else.

1. **Target users** — who forks this repository, and who should not.
2. **The adoption path** — what a fork does before its first verified run on its own project.
3. **Product acceptance criteria** — what must hold for the product to be worth using.

## What this document does not own

It restates nothing. Every topic below already has a canonical home, and this document
links there rather than repeating it.

| Topic | Canonical document |
|---|---|
| Why the framework exists | [`00-OVERVIEW.md` § Why this is needed](00-OVERVIEW.md#why-this-is-needed) |
| Technical non-goals | [`00-OVERVIEW.md` § Explicit Non-Goals](00-OVERVIEW.md#explicit-non-goals) |
| Runtime and dependency constraints | [`00-OVERVIEW.md` § Constraints](00-OVERVIEW.md#constraints) |
| Pipeline stages and the CLI | [`00-OVERVIEW.md`](00-OVERVIEW.md) |
| Terms used here | [`01-CONCEPTS.md`](01-CONCEPTS.md) |
| What isolation does and does not guarantee | [`05-EXECUTION-ISOLATION.md`](05-EXECUTION-ISOLATION.md), [`09-SECURITY.md`](09-SECURITY.md) |
| Metric definitions | [`11-EVALUATION.md`](11-EVALUATION.md) |
| Milestone scope | [`12-ROADMAP.md`](12-ROADMAP.md) |

**If a requirement here ever contradicts one of those documents, the design document wins
and this one is wrong.** A product requirement does not get to redefine a contract.

## Target users

### The fork owner — primary

Someone who owns a codebase, wants a coding agent to work in it, and is unwilling to take
the agent's word for what it did.

What is assumed about them:

- They already have a Git repository, Python 3.11+ and an authenticated vendor CLI.
- They run the harness **inside their own project**, never in this repository.
- They will write acceptance criteria by hand. `spec`, `plan` and `tasks` emit skeletons,
  and `analyze` blocks while those skeletons are still empty.

The last point is a requirement, not an observation. The product must never assume the
user will accept generated acceptance criteria unread, because an agent that writes its
own passing condition is the failure this framework exists to prevent.

### The harness tuner — secondary

Someone comparing configurations — a different vendor, profile or context budget — who
needs a number rather than an impression. They use `harness eval` and read the metrics
defined in [`11-EVALUATION.md`](11-EVALUATION.md).

### Not a target user

- Someone who wants a hosted service, a daemon or a web UI. The kernel has none.
- Someone who wants the agent to merge its own work. `ship` is a human command.
- Someone who wants a single-command wrapper that needs no human input. The human writes
  the acceptance criteria, and that step is not removable.

## The adoption path

Two repositories are in play: this one, which is installed once, and the target project,
where every run happens. Confusing the two is the likeliest first mistake, so each step
below carries its own check.

| # | Step | What it changes | Check |
|---|---|---|---|
| 1 | Install from a clone | nothing in the target project | `harness --help` runs |
| 2 | `harness init` in the target repository | creates `.harness/` | `harness doctor` reports no findings |
| 3 | Write the project's rules into `.harness/constitution.md` | control plane | the rules reach every task prompt, untruncated |
| 4 | Replace the `mock` adapter slot with a real vendor | `.harness/config.yaml` | `harness doctor` runs adapter preflight |
| 5 | Read `command_policy` and adjust it to the project | `.harness/config.yaml` | the default is fail-closed, so nothing new runs unattended until it is listed |
| 6 | Run one full cycle and fill in the acceptance criteria by hand | `spec` · `plan` · `tasks` outputs | `harness status` reports a verified task |

**No step on this path may require editing harness source.** A fork adapts the harness
through its control plane, not through a patch. This is what makes the repository a fork
template rather than a codebase to be rewritten per project.

## The customization surface

Everything that legitimately differs between projects is set in one of these places.

| What differs per project | Where it is set | Canonical document |
|---|---|---|
| Which agent CLI runs | `adapters` and `defaults.adapter` in `.harness/config.yaml` | [04](04-AGENT-ADAPTER.md) |
| A vendor with no built-in adapter | the `generic_cli` adapter type, same file | [04](04-AGENT-ADAPTER.md) |
| Rules the agent may never break | `.harness/constitution.md` | [01](01-CONCEPTS.md), [07](07-CONTEXT-SELECTION.md) |
| Which commands may run unattended | `command_policy` in `.harness/config.yaml` | [06](06-VERIFICATION-REVIEW.md) |
| How isolated execution is | `defaults.profile` | [05](05-EXECUTION-ISOLATION.md) |
| How much runs in parallel | `defaults.max_parallel` | [05](05-EXECUTION-ISOLATION.md) |
| What counts as done | the acceptance criteria in `tasks/*.task.yaml` | [03](03-DATA-MODEL.md), [06](06-VERIFICATION-REVIEW.md) |

Nothing in that table is a source edit. A vendor the harness has never heard of is reached
through `generic_cli` and configuration alone. If some project ever cannot be served
without patching `harness/`, that is a defect in the customization surface rather than a
task for the fork owner.

## Product acceptance criteria

These are product-level, and each one is already observable. None of them introduces a new
check; they name the evidence that the product does what it claims.

| # | Criterion | Where it is observable |
|---|---|---|
| 1 | A fork reaches a working control plane on its own project without editing harness source | `test_doctor_passes_on_a_freshly_initialized_repo`, `test_init_creates_the_control_plane` |
| 2 | Changing the vendor CLI is a configuration change | `test_generic_cli_is_registered`, `test_the_adapter_passes_the_conformance_suite` |
| 3 | The agent's self-report never decides the outcome | `test_agent_result_has_no_success_field`, [06](06-VERIFICATION-REVIEW.md) |
| 4 | Nothing reaches the user's branch without a human command and a fresh gate | `test_ship_requires_a_fresh_converge`, `test_an_open_debt_blocks_ship_until_waived` |
| 5 | An interrupted run resumes from the journal rather than a hand-edited state file | `test_doctor_rebuilds_a_stale_state_snapshot`, `test_run_resume_picks_up_the_named_run` |
| 6 | The kernel stays usable with the whole optional layer removed | `test_the_pipeline_finishes_without_the_option_layer` |
| 7 | A configuration change can be shown to be an improvement rather than asserted | `harness eval`, [11](11-EVALUATION.md) |

A criterion that stops being observable is a criterion that has been lost. Removing the
evidence while keeping the claim is the failure this table exists to catch.

## Out of scope for the product

These are product decisions, distinct from the technical non-goals in
[`00-OVERVIEW.md`](00-OVERVIEW.md#explicit-non-goals).

- **A fork's own configuration does not come back upstream.** A `.harness/` directory, a
  vendor choice or an editor-plugin setting belongs to the project that made it. This
  repository stays vendor-neutral so that the next fork starts from neutral ground.
- **Per-project presets are not shipped here.** A preset that only one kind of project
  wants belongs in that project's fork.
- **Authoring the acceptance criteria is not automated.** Generating them from the spec
  would restore exactly the self-graded loop described in
  [`00-OVERVIEW.md` § Why this is needed](00-OVERVIEW.md#why-this-is-needed).
