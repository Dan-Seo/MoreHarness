# PRD — (project name)

> **STATUS: UNFILLED TEMPLATE.**
>
> While this block is still here, this repository is the Harness Framework itself
> and a coding agent should read it that way.
>
> Delete this block and fill in the sections below. From that moment the
> repository **is your project**, and the harness is the infrastructure it ships
> with. A coding agent reads this file before anything else and treats your
> requirements, not the framework's, as the work.

Write this file in whatever language your team works in. Nothing here has to be
English, and nothing here has to match the framework's documents.

---

## What this repository builds

(One paragraph. What the product is and what it does for someone. Not how it is
built.)

## Who it is for

(Who uses it, and what they are doing when they reach for it. Name who it is
explicitly not for, if that is a live question.)

## What it must do

Each row is one requirement, stated so that it can be checked rather than
argued about.

| # | Requirement | How we know it is met |
|---|---|---|
| 1 | | |
| 2 | | |
| 3 | | |

## What it does not do

(The things a reader would reasonably expect and will not get. Saying this
plainly is what keeps scope from drifting.)

## Constraints

(Language, runtime, platform, dependency limits, deadlines, anything that rules
an option out before it is proposed.)

## How this project uses the harness

The harness is infrastructure here, not the product. Fill in what differs from
the defaults:

- **Agent adapter**: (which vendor CLI, set in `.harness/config.yaml`)
- **Execution profile**: (`worktree` unless changed)
- **Project rules**: `.harness/constitution.md`
- **Commands allowed to run unattended**: `command_policy` in `.harness/config.yaml`

The full customization surface is in [`docs/13-PRD.md`](docs/13-PRD.md).

---

## Notes for a coding agent reading this repository

- **This file describes the product. `AGENTS.md` describes how to work on the
  framework.** When they disagree about what the work is, this file wins. When
  the change is to `harness/`, `docs/` or `tests/`, the framework's rules in
  `AGENTS.md` still bind, because that is still framework code.
- **`docs/13-PRD.md` is a different document.** It is the harness framework's own
  product requirement, about who forks the framework. It is not about this
  project.
- **This file is not injected into agent task prompts.** The harness builds task
  prompts from `.harness/constitution.md`, the spec and the task card. A rule
  that must reach every task belongs in the constitution as well, kept short.
