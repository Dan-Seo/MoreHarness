# PRD — (project name)

> **STATUS: UNFILLED TEMPLATE.**
>
> Fill in the sections below and delete this block. Until then a coding agent has
> no statement of what this repository builds. If the repository also carries the
> Harness Framework's own documents, the agent reads those instead and assumes the
> framework is the product.

Write this file in whatever language your team works in.

---

## What this repository builds

(One paragraph. What the product is and what it does for someone. Not how it is
built.)

## Who it is for

(Who uses it, and what they are doing when they reach for it. Name who it is
explicitly not for, if that is a live question.)

## What it must do

Each row is one requirement, stated so that it can be checked rather than argued
about.

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

## The rest of the skeleton

This file says **what** is being built. Two more say **how**, and a coding agent
reads them alongside this one.

- `project/CONVENTIONS.md` — stack, the commands to build and test with, the
  rules that must not be broken.
- `project/ARCHITECTURE.md` — where the code lives, how data moves, which
  decisions were made and what they cost.

---

## Notes for a coding agent reading this repository

- **This file describes the product.** If this repository also carries the
  Harness Framework itself, `AGENTS.md` describes how to work on the framework
  and still binds any change under `harness/`, `docs/` and `tests/`. It does not
  describe this project.
- **`docs/13-PRD.md`, where it exists, is a different document.** It is the
  framework's own product requirement, about who forks the framework. It is not
  about this project.
- **This file is not injected into agent task prompts.** The harness builds those
  from `.harness/constitution.md`, the spec and the task card. A rule that must
  reach every task belongs in the constitution as well, kept short.
