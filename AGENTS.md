# Working on Harness Framework

These instructions apply to the whole repository and to every coding agent.
Start with `README.md` and the document map in `docs/00-OVERVIEW.md`.

## Setup and verification

- Python 3.11+ and Git are required. Runtime dependencies are stdlib, PyYAML,
  and jsonschema; justify additions and keep vendor SDKs out of the kernel.
- Install development dependencies: `python -m pip install -e ".[dev]"`.
- Inspect the CLI without creating state: `python -m harness --help`.
- Run the offline suite: `python -m pytest -q`. Vendor adapter tests use fake
  subprocesses and need no vendor account or network access.
- For adapter changes, first run
  `python -m pytest tests/test_adapters.py tests/test_vendor_adapters.py -q`.
  Run `tests/test_kernel_only.py` when changing imports or kernel dependencies.
- The optional Codex live test is documented in `README.md`. It consumes model
  usage; run it when the task calls for live integration testing and report its
  result separately from the offline suite.
- On Windows, use a native `codex.exe` path if `codex` is not on PATH. Do not
  enable `shell=True` to work around an npm shim.
- Tests create temporary Git repositories and external worktrees. A sandbox
  denial on system temp is an environment failure. Report it and rerun with
  permitted access instead of weakening tests or treating it as success.

## Repository map

- `harness/adapters/`: subprocess protocol, vendor adapters and conformance.
- `harness/exec/`: task execution, worktrees, TDD, verification and review.
- `harness/context/`: selection, provenance and budgets.
- `harness/eval/`: fixture materialization, evaluation arms and reporting.
- `harness/resources/`: packaged schemas and authoring templates.
- `tests/`: contract tests, including the explicitly enabled Codex live test.
- `evals/fixtures/`: deterministic regression fixtures.
- `evals/capability/`: capability fixtures and recorded evaluation evidence.
- `docs/`: English contracts; `docs/ko/`: Korean translations.
- `build/`, `*.egg-info/`, caches and run outputs are generated artifacts.
  Edit source files rather than their build copies.

## Contracts and change process

Each concept has exactly one canonical design document. English is canonical.
Update the corresponding Korean paragraph when changing a contract. Link to a
definition instead of repeating it in another design document.

| Concept | Canonical document |
| --- | --- |
| Dependency direction and kernel/optional boundary | `docs/02-ARCHITECTURE.md` |
| Verdicts, state, events and file layout | `docs/03-DATA-MODEL.md` |
| Adapter protocol and conformance | `docs/04-AGENT-ADAPTER.md` |
| Profiles and isolation guarantees | `docs/05-EXECUTION-ISOLATION.md` |
| Adjudication, TDD and Command Policy | `docs/06-VERIFICATION-REVIEW.md` |
| Context provenance and trust | `docs/07-CONTEXT-SELECTION.md` |
| Ship gates and debt ledger | `docs/08-CONVERGENCE-LEARN.md` |
| Threat model and non-guarantees | `docs/09-SECURITY.md` |
| Failure classification and recovery | `docs/10-FAILURE-RECOVERY.md` |
| Evaluation and metrics | `docs/11-EVALUATION.md` |

1. Identify the relevant contract before changing behavior. If it changes,
   update its document first and remove the superseded wording. Keep revision
   history in Git rather than in the document body.
2. Write a contract test and observe its failure before implementing a behavior
   change. Test externally visible behavior rather than implementation structure.
3. Make the smallest coherent change, run relevant tests, then the offline suite.
   Keep English and Korean usage instructions consistent.
4. Report what was tested, the result and any untested integration boundary.
   A fake CLI test does not prove that a real vendor agent completed a task.

M0–M9 are implemented. For new capabilities, consult the implementation rehearsal
checklist in `docs/12-ROADMAP.md`. Kernel size is an observation, not a line quota.
Use conventional commit messages when a commit is requested.

## Implementation and review rules

- Adjudicate only from harness-owned evidence. Claims, handoffs and agent exit
  codes cannot prove success. Missing or malformed claims must not prevent an
  otherwise verified result.
- Adapters launch processes and return artifact paths. Parsing, validation and
  verdicts belong to the harness. Preserve generic CLI equivalence and conformance.
- Apply `policy.py` to task acceptance, preconditions, health checks, verifiers
  and fixture commands according to docs/06. Preserve argv lists, `shell=False`
  and fail-closed approval behavior.
- Keep dependencies acyclic: `models ← events/store/dag/risk/probes/policy ←
  exec/context ← eval ← cli`. Evaluation must not import the CLI. The kernel
  must remain usable without optional modules.
- Append and fsync the journal before updating state. Events are immutable;
  corrections are new events. Metrics are journal projections, not counters.
- Do not add verdicts. Consult docs/03 for the distinction between verdict and state.
- Keep worktrees and outboxes outside the target repository. Read constitution
  and config from the main repository, never an agent-edited worktree.
- Keep `sys.exit` in `cli.py`; other modules return values or raise exceptions.
- Describe worktree protections as authorization and detection, not OS isolation.
  Preserve the profile limits and non-guarantees in docs/05 and docs/09.
- Use disposable repositories for `init`, `run`, `doctor` and `ship` smoke tests;
  they may mutate state or Git branches. Do not overwrite the checked-in
  capability report with an unrelated smoke test.
