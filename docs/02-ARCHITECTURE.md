# 02 · Architecture

## Module layout

```
harness/
  cli.py              # Entry point. sys.exit is called only here.
  config.py           # Loads and validates .harness/config.yaml
  models.py           # Pure data types. Does not import any other harness module.
  events.py           # Event types and payload schemas
  schemas.py          # Reads the jsonschema definitions in resources/schemas/ and builds validators
  store.py            # journal append + state projection + reconstruction
  dag.py              # Dependency graph, topological sort, ready-set
  git.py              # git call wrapper (diff, worktree, branch, merge)
  risk.py             # effective_risk computation
  probes.py           # precondition checks, environment verifier
  policy.py           # Command Policy — allow / deny / require_approval
  errors.py           # Exception types. Imports nothing. Classification follows the table in 10.
  paths.py            # Path matching for the glob dialect in 05. Imports nothing.
  learn.py            # knowledge card promotion/retirement
  spec.py             # Authoring-stage scaffold — spec/clarify/plan/tasks (08)
  analyze.py          # Pre-implementation gate (08)
  converge.py         # Post-implementation gate and ship (08)

  adapters/
    base.py           # AgentAdapter protocol, AgentRequest/AgentResult
    registry.py       # name → adapter resolution
    conformance.py    # The test suite every adapter must pass
    mock.py           # Deterministic. The default for regression eval and CI
    generic_cli.py    # Invokes an arbitrary CLI from configuration alone
    claude_cli.py     # Adds optional capabilities
    codex_cli.py      # Adds optional capabilities

  exec/
    workspace.py      # worktree and outbox creation and cleanup, integration branch merge
    runner.py         # Executes one task's attempt (sequential)
    scheduler.py      # Parallel scheduling, path-conflict serialization, merge queue
    verify.py         # AC execution, diff adjudication, path scope, verdict computation
    handoff.py        # outbox artifact normalization, required gate, TaskOutput merge
    review.py         # Review waves, finding merge, fixer

  context/
    builder.py        # Layer assembly, provenance marking, prompt compartment convention wording
    repomap.py        # Repository structure summary
    slicing.py        # Excerpting by document anchor and symbol unit
    budget.py         # Token budget allocation and drops

  eval/               # Outside the kernel
    fixtures.py  arms.py  metrics.py  report.py
    swebench.py       # External benchmark → fixture converter

  resources/          # Runtime resources distributed with the package
    schemas/          # jsonschema definitions (claim, handoff, task, spec, event, findings)
    templates/        # spec / plan / task templates

evals/fixtures/<case>/
```

## Distribution

The harness is distributed as a `pip install`-able package and **operates from the installed package
alone.** If putting it on another repository requires a clone of this repository, it is not a framework
but a pile of scripts.

- The resources read at runtime (jsonschema definitions, authoring templates) are all inside
  `harness/resources/`. **The harness never reads sibling directories of the repository root at runtime.**
- The target repository is designated by `--repo`. The harness code does not need to be inside that repository.
- `evals/fixtures/` and `tests/` are development assets of this repository, not runtime resources.
  They do not go into the distribution.

## Dependency direction

```
models · errors · schemas · paths
  ↑
events · store · dag · git · risk · probes · policy
  ↑
exec/* · context/* · spec · analyze · converge
  ↑
eval/*
  ↑
cli
```

Rules:

- **Circular dependency is forbidden.** CI checks the import graph.
- `models`, `errors`, `schemas` and `paths` do not import any harness module. The four are the same lowest layer, and they do not import each other either.
- `adapters/*` depend only on `models` and `errors`. They do not expose a vendor SDK anywhere in the kernel.
- **`cli` calls `eval`.** `eval` does not import `cli`. The lower-level APIs `eval` depends on are only `store` (reading the journal), `exec/runner` (execution), `adapters/registry` (selecting the adapter per arm), `spec` (fixture skeleton), and `models`.
- The only module that imports `eval` is `cli`.
- `sys.exit` exists only in `cli.py`. Other modules raise exceptions or return values. That is what lets it be used as a library and tested.

## Kernel and optional

**Kernel** — the pipeline must run to the end with this alone.

```
models  events  store  dag  git  probes  policy  errors  config  schemas  paths
exec/{workspace, runner, verify, handoff}
adapters/{base, registry, conformance, mock, generic_cli}
cli
```

**Optional** — the kernel works with these removed.

```
exec/scheduler   (parallelism)
exec/review      (independent review)
spec · analyze · converge   (spec pipeline — authoring and gates)
risk             (tier decision — without it, declared_risk is used as-is)
context/*        (advanced selection — without it, only the files named in the task contract are included)
learn            (knowledge accumulation)
adapters/{claude_cli, codex_cli}
eval/*
container profile
```

**M8's completion criteria are to prove by test that the kernel works with the entire optional layer removed.** This test enforces that the kernel/optional boundary is real.

## Simplicity observed metric (soft budget)

About 1,500 lines of kernel is **an observed metric, not a pass condition.** Exceeding it is not a failure but a signal to revisit module boundaries.

**Sacrificing readability to hit the line count is forbidden.** Do not meet the metric by cramming logic onto one line, shortening names, or deleting comments.

`policy.py` and `exec/handoff.py` belong to the kernel but are responsibilities added later, so when the actual count is taken, record the share of these two separately. If these two are the cause of the kernel growing, that is information, not a defect.

## Extension points

When adding a new feature, only these three may be created.

1. **A new adapter** — one file in `adapters/`. Passing the conformance suite is the pass condition. See 04.
2. **A new probe kind** — one `kind` in `probes.py`. One value is added to the precondition grammar.
3. **A new event type** — added to `events.py` and reflected in `store.py`'s fold. It must not change the meaning of existing events.

For any other feature, **state first which document's contract it changes** and then proceed. A feature that changes no contract has no reason to be in the kernel.
