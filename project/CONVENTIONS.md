# Conventions — (project name)

> **UNFILLED TEMPLATE.**
>
> Fill this in and delete this block. A coding agent reads this file to learn what
> the project is built with and which commands to run. Until it is filled in the
> agent has to guess, and it guesses from whatever files happen to be lying around.

Write in whatever language your team works in.

## Stack

- **Language and runtime**:
- **Framework**:
- **Package manager**:
- **Storage**:
- **External services this depends on**:

## Commands

An agent runs these. Every one has to work from a fresh clone.

| Purpose | Command |
|---|---|
| Install dependencies | |
| Run locally | |
| Build | |
| Test | |
| Lint | |
| Type check | |

Two things follow from this table, and both are easy to miss.

- **The harness verifies with these commands.** An acceptance criterion in a task
  card is one of them, so a command that does not exist here cannot become a
  passing condition.
- **Every command the harness runs must pass `command_policy`** in
  `.harness/config.yaml`, which is fail-closed by default. Add the commands above
  to it, or the harness stops and asks for approval each time.

## Code rules

Mark the ones that must never be broken as **CRITICAL**.

- CRITICAL:
- CRITICAL:
-

Copy the CRITICAL rules into `.harness/constitution.md` as well. That file is the
one the harness puts into every agent task prompt, and the context budget never
truncates it. This file is not in that path.

## Definition of done

(What has to be true before a change here is finished. Tests written first, lint
clean, docs updated, whatever your team actually holds to.)
