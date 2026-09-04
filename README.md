# Harness Framework

> **Agents propose. Harness verifies. Evidence decides.**

A framework that runs coding agents; **the harness itself checks whether the result is real**.

The agent writes code and reports what it did. The harness does not use that report as a basis for
adjudication. It runs the acceptance criteria itself, reads the git diff itself, and adjudicates the
changed paths itself. Why it was built that way, and the whole pipeline, are in [`docs/00-OVERVIEW.md`](docs/00-OVERVIEW.md).

## State

`0.1.0`. Milestones M0–M9 are all implemented and 628 tests pass. That the kernel works with the entire
optional layer removed is enforced by `tests/test_kernel_only.py` in a subprocess.

The scope confirmed in real use is still narrow — one adapter, the `claude` CLI; one capability eval
fixture (`evals/capability/slugify`); one profile, `worktree`. Everything else is verified only by tests.

## Requirements

- Python 3.11+
- git
- Runtime dependencies: `PyYAML`, `jsonschema` (everything else is stdlib)
- To actually run an agent, that vendor's CLI (e.g. `claude`). The adapter invokes it only as a subprocess.

## Installation

It is not on PyPI. Install it from a clone.

```bash
git clone https://github.com/Dan-Seo/MoreHarness.git
cd MoreHarness
pip install .
```

That installs the `harness` command. From then on you run it inside **your own** project
repository, not in this one. To use it without installing, `python -m harness` from the clone root
is the same.

## 5-minute quickstart

```bash
cd <your-repo>            # must be a git repository
harness init              # create .harness/ (idempotent)
harness spec "a function that turns a user name into a slug"   # a Spec skeleton tagged R-###
harness plan              # Spec → Plan
harness tasks             # Plan → tasks/*.task.yaml (DAG)
                          # ← here a human fills in the spec's [NEEDS CLARIFICATION] and the task's AC
harness analyze           # pre-implementation gate. If they are still empty, it blocks here
harness run               # execute the DAG
harness status            # verdict, open_debts, human_required
harness converge          # post-implementation gate — coverage · drift · debt
harness ship              # merge the integration branch into the user's branch
```

`spec` · `plan` · `tasks` produce only a skeleton. A human fills it in, and `run` only proceeds once `analyze` passes.

The default adapter in the `.harness/config.yaml` that `init` creates is `mock`. To attach a real agent,
change that slot.

```yaml
defaults:
  adapter: claude

adapters:
  claude:
    type: claude_cli
    extra_args: ["--permission-mode", "acceptEdits", "--max-turns", "30"]

agent_timeout_s: 900
```

`command_policy` in the same file screens **every command the harness runs**. The default is
fail-closed, and what is visible there is everything approved for automatic execution. Read it before
use and adjust it to the project.

## TDD enforcement mode

When a task declares `development.mode: tdd`, the harness **observes the red→green order itself.**
An agent's report that it "did TDD" is not evidence.

```yaml
# tasks/T-003.task.yaml
acceptance:
  - cmd: ["python", "-m", "pytest", "-q", "tests/api"]
    expect_fail_before: true      # the red gate confirms red with this command

development:
  mode: tdd
  test_paths:           ["tests/api/**"]
  implementation_paths: ["src/api/**"]
```

One attempt is dispatched twice.

```
test-author  →  the harness looks at the diff (touching an implementation path is rejected)
             →  it commits the tests to pin the observation base
red gate     →  the harness runs the ACs itself. If green, the implementation is not dispatched
implementation → the harness looks at the diff against the red gate commit
             →  modifying or deleting a test is detected even if it is hidden in a commit
green gate   →  the existing differential adjudication — red→green and regression together
```

The existing conditions for `verified` are not weakened. TDD only adds evidence. The contract is
canonical in [`docs/06-VERIFICATION-REVIEW.md`](docs/06-VERIFICATION-REVIEW.md).

## What is not guaranteed

Read before starting — this framework has explicitly narrowed the scope of what it guarantees.

- What the execution profiles guarantee and what they do not: [`docs/05-EXECUTION-ISOLATION.md`](docs/05-EXECUTION-ISOLATION.md)
- The threat model and the list of non-guarantees: [`docs/09-SECURITY.md`](docs/09-SECURITY.md)

We do not summarize them. Those two documents are canonical.

## Measurement

Improvement is measured only as a projection of the journal. The metric definitions are in [`docs/11-EVALUATION.md`](docs/11-EVALUATION.md),
and the result of the first capability eval actually run is in [`evals/capability/eval-report.md`](evals/capability/eval-report.md).

```
harness eval run --fixtures evals/capability --arms raw,harness-full --repeat 3
```

## Documents

The 13 design documents are in `docs/`, and the canonical definition of any one concept lives in exactly
one document. Start from the document map in [`docs/00-OVERVIEW.md`](docs/00-OVERVIEW.md).
Korean translations of every document live in [`docs/ko/`](docs/ko), and of this page in [`README.ko.md`](README.ko.md).

## Development

```
pytest
```

Read [`CLAUDE.md`](CLAUDE.md) before contributing — to change a contract, fix the canonical document
first, and write the test first.

## License

MIT. The full text is in [`LICENSE`](LICENSE).
