# 05 · Execution and Isolation

This document holds the **canonical definition of what an execution profile guarantees and what it does not guarantee**.

## Profiles

| Profile | Where it executes |
|---|---|
| `safe` | The main worktree. For `readonly`/`analysis` tasks where no change is expected |
| `worktree` | A git worktree created outside the repository. **The default for implementation** |
| `container` | Inside a container. The only one that provides OS-level isolation |
| `unsafe` | Executes with no constraints. It is never the default |

---

## What is guaranteed and what is not — canonical

|  | `safe` | `worktree` | `container` |
|---|---|---|---|
| cwd fixed | yes | yes | yes |
| branch/worktree separation | no | yes | yes |
| git diff scope verification | yes | yes | yes |
| post-hoc check of allowed paths | yes | yes | yes |
| mistake · contamination detection | yes | yes | yes |
| **OS filesystem isolation** | **no** | **no** | yes |
| **credential isolation** | **no** | **no** | yes |
| **network isolation** | **no** | **no** | configurable |
| **malicious agent containment** | **no** | **no** | partial |

### Phrasing convention

This document and every other document use the following phrasing.

> **Not "the agent cannot write", but "the harness does not approve the write and detects repository-scope violations. OS-level containment requires the `container` profile."**

A normal CLI agent running with the same OS user privileges can access other paths, `$HOME`, credential files, and the network. `worktree` does not block this. Documentation that pretends to block it is forbidden, because it puts the user into a false sense of security.

### The three things actually done about `.harness/**`

1. Forbids access in the agent prompt and the tool policy.
2. Places the worktree outside the repository, **excluding it from the default access path**.
3. **Detects** it as a `path_violation` if the diff touches `.harness/`.

We do not claim an absolute OS-level write prohibition on the strength of the `worktree` profile alone.

---

## `container`

The only profile that provides OS-level isolation. The harness executes **only the agent process**
inside the container. AC, precondition and health commands and diff observation run unchanged on the
host — adjudication is harness-owned evidence, and that evidence is not produced inside the same
enclosure as the agent.

The workspace lifecycle is the same as `worktree`. A container is **a way of executing, not a
workspace layout.**

```yaml
container:                     # a config key owned by 05
  runtime: docker              # the executable's name. docker | podman
  image: "example/agent:1"     # required
  network: null                # null means the runtime default. "none" cuts the network off
  mounts: []                   # extra mounts. a list of "<host>:<container>[:ro]" strings
```

Wraps the argv the adapter assembled in the following form.

```
<runtime> run --rm
  -v <workspace>:<workspace>  -v <outbox>:<outbox>
  -w <workspace>
  [-i]                         # if prompt_delivery is stdin
  -e <NAME> ...                # names only, not values
  [--network <network>]
  [-v <mount> ...]
  <image>
  <argv assembled by the adapter...>
```

- **Paths are identical on host and in the container.** The workspace and the outbox are mounted at
  the same paths, so the paths written in the prompt and the manifest remain valid inside the container.
- **`.harness/` is not mounted.** The control-plane is not visible inside the container.
- Environment variables are passed by `-e NAME` — **names only**. The value is delivered through the
  env of the runtime CLI process, so no secret is left in the process list. Which names are passed is
  exactly the `env_passthrough` convention of 04.
- **The network default is the runtime's default.** The agent CLI has to reach the model API, so the
  harness does not cut it off on its own. To cut it off, declare `network: none`.
- The owner of files the container created is decided by the runtime. The harness does not correct it.

If the `runtime` executable is absent, that is a missing prerequisite, so verdict `blocked`. An
undeclared `image` is a misconfiguration, so `error`. 10 is canonical for the classification table.

**Under `container`, the prerequisite check targets `runtime`, not the adapter's executable.** The
agent CLI is inside the image and may be absent on the host, so the adapter preflight's
`missing_prerequisite` does not lead to `blocked` under this profile. A misconfiguration
(`misconfigured`) is still `error` (04's preflight classification).

---

## `unsafe`

- **It is never the default.**
- It is enabled only if an explicit allowance in config **and** an explicit CLI flag are **both** present.
- It prints a banner when enabled.
- It is recorded in the run manifest.
- The review tier is automatically escalated.

---

## Workspace lifecycle

```
1. Prepare      create a git worktree at <system temp>/harness/<repo-key>/<run-id>/<task-id>/worktree
                branch: harness/<run-id>/<task-id>
2. outbox       create ../outbox/attempt-<n>/. It is outside the worktree.
3. Execute      the adapter executes the process with cwd = worktree
4. Observe      compute changed_files / created_files / diff_stat from git diff
5. Adjudicate   path scope + AC post (06)
6. Integrate    if it passed the evidence and the handoff gate, merge. 06 is canonical for the adjudication order.
7. Clean up     remove the worktree on success. On failure, preserve it and record the path.
```

A failed worktree is left behind so that a human can investigate it. `harness doctor` cleans up orphan worktrees.

**The harness always reads `constitution.md` and `config.yaml` from the main repository.** The copy inside the worktree is repository content the agent can modify, so it is not used as control-plane input.

---

## The integration branch

Making a branch per task scatters the results. With no place to gather, a downstream task does not see the upstream's changes and the `depends_on` declaration becomes meaningless.

```
run start    create harness/<run-id>/integration from HEAD
task prepare create harness/<run-id>/<task-id> from the current tip of the integration branch
task end     commit the worktree's changes to the task branch
integration  merge the task branch into the integration branch
```

- **A task worktree branches from the current tip of the integration branch, not from HEAD at run start.** What an earlier task produced has to actually be in the later task's workspace.
- **The user's branch is never changed even once during the run.** To throw away a run's results, delete `harness/<run-id>/*`; moving them onto the user's branch is `ship`'s job. See 08.
- **The harness makes the commits.** Whether the agent committed has no effect on adjudication. The input to adjudication is the diff, not the commit; the commit is only the means that makes the merge possible.
- A merge conflict is state `integration_conflict` with no verdict. See 10.
- **There is no integration for the `safe` profile.** There is no worktree, so the changes are already in the main worktree.

---

## Path scope adjudication

```
effective_forbidden = task.forbidden_paths ∪ config.forbidden_paths
                      (config.forbidden_paths always includes .harness/**)
```

Adjudication is **post-hoc**. After the agent has finished executing, `git diff --name-status` yields the real change list, and

- if a path outside `allowed_paths` is present → `path_violation` event → verdict `rejected`
- if a path inside `effective_forbidden` is present → `path_violation` event → verdict `rejected`

glob matching is performed after normalizing to POSIX paths relative to the repository root. Symlinks are not followed; adjudication uses the link's own path.

### The glob dialect

It is the gitignore family.

| Pattern | Match |
|---|---|
| `*` | zero or more characters, not crossing `/` |
| `?` | one character that is not `/` |
| `**` | zero or more path segments. `src/api/**` covers `src/api` itself and everything beneath it |
| anything else | literal |

- Patterns are always relative to the repository root. `*.py` points only at the root's `.py` files, not at subdirectories.
- **If `allowed_paths` is not declared, there is no restriction on the allowed scope.** `effective_forbidden` still applies here as well. Making every task declare its paths is `analyze`'s job, not adjudication's. See 08.
- Matching looks only at the path string. It does not look at whether that file actually exists now — deleted paths are also subject to adjudication.

---

## Parallel execution and the integration queue

The parallel scheduler is optional (02). At `max_parallel` 1 it works with the kernel's sequential runner alone; at 2 or more the scheduler takes over execution. `max_parallel` is **the cap on the number of concurrently executing tasks**.

### Scheduling

```
ready_set = { t | t.depends_on are all verified }
selecting what to run concurrently:
  walk ready_set in topological order (task id order within the same layer),
  skip a task that overlaps one already running or already selected (serialization)
  stop when running + selected reaches max_parallel
```

Running tasks whose paths overlap at the same time makes diff attribution ambiguous and adjudication meaningless. **If they overlap, serialize** — that is the only rule.

### Overlap determination

The determination is conservative. **Over-serialization only costs parallelism, but under-detection contaminates adjudication.** It looks in the following order.

1. A `safe` profile task shares the main worktree, so it **overlaps every task.**
2. A task that does not declare `allowed_paths` has no restriction on its allowed scope, so it **overlaps every task.**
3. Two declared sets are looked at pattern pair by pattern pair — take each pattern's literal prefix up to the first wildcard (`*` · `?`), and **if one is a prefix of the other, they overlap.** `src/api/**` and `src/web/**` are separate; `src/**` and `src/api/**` overlap.

Skipped tasks are not discarded. They are considered again in the selection after the overlapping task finishes, and because they branch from the integration branch tip at that moment they see the earlier task's results.

`analyze` reports in advance the `allowed_paths` conflicts between tasks declared as parallelizable. See 08.

### Integration

- **Integration is always serial.** It is not subject to parallelization.
- A task that passed the evidence conditions and the handoff gate enters the merge queue. **verdict `verified` is recorded only after the merge succeeds.**
- On a merge conflict it goes to state `integration_conflict` with no verdict, and passes to replan or to a human.
- The journal writer is still exactly one orchestrator process. See 03.

---

## Config keys owned by 05

```yaml
forbidden_paths: [".harness/**"]   # the global forbidden list. Union with a task's forbidden_paths.
container: null                    # the execution contract of the container profile. The "container" section above.
```

**`.harness/**` cannot be removed from this list.** Even if you delete it from the configuration, the harness puts it back. A configuration that lets the agent fix the control-plane must not exist.
