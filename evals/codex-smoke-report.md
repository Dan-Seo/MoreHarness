# Codex compatibility smoke test

Date: 2026-09-06 (Asia/Seoul).

The real Codex CLI completed a task through the harness with verdict `verified`.
The final opt-in pytest run passed: **1 passed in 53.40s**.
The full offline regression suite (`python -m pytest -q`) also passed:
**645 passed, 1 skipped in 321.35s**. The skipped test is the opt-in live test
reported separately above.

| Environment | Value |
| --- | --- |
| Host | Windows |
| Python | 3.13.9 |
| Codex CLI | 0.153.4, installed native executable and existing authentication |
| Harness profile | `worktree` |
| Codex sandbox | `workspace-write`, with the attempt outbox added by the adapter |
| Attempt budget | One attempt, 180-second agent timeout, no handoff repair |

The test seeded a disposable Git repository containing an unimplemented
`double(value)` function, an acceptance script, and an `AGENTS.md` instruction
requiring an exact docstring. It verified:

- The final function returned the correct values for negative, zero and positive
  inputs and had the docstring required by `AGENTS.md`.
- Only `calculator.py` changed on the integration branch.
- Claim and handoff artifacts were read from the external outbox, validated and
  recorded as `claim_received` and `handoff_received`.
- The harness's acceptance result was `green`, with `differential: proven`, and
  the final task verdict was `verified`.

Reproduce with the command in the [development guide](../README.md#development).
The test source is [`tests/test_codex_live.py`](../tests/test_codex_live.py).
The successful run's local evidence is retained under:

```text
<system temp>/harness-codex-smoke-1d152b61f40341ddb92c361fdc03ddb5/
  repo/.harness/runs/codex-smoke/journal.jsonl
  repo/.harness/runs/codex-smoke/state.json
  repo/.harness/runs/codex-smoke/tasks/T-001/transcript/attempt-1.log
```

This is an integration smoke test for one task on one Windows environment.
It does not establish comparative coding capability, multi-task performance,
container isolation, or compatibility with every Codex version. The existing
Claude capability evaluation remains a separate result.
