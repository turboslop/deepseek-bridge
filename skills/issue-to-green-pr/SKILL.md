---
name: issue-to-green-pr
description: Multi-agent repository workflow for taking a GitHub issue or implementation task in turboslop/deepseek-bridge from current main through planning quorum, branch creation, implementation, local validation, review, QA, pull request creation, CI monitoring, iterative fixes, and auto-merge. Use when asked to pick up an issue, work an issue, implement a GitHub task, create a PR, get checks green, or follow the repo's standard task-to-PR flow.
---

# Issue To Green PR

## Local Gang Of Four

Run this workflow as a local Gang of Four:

- quorum: planner/architect, DevOps/platform expert, and developer maintain the shared decision graph, own the plan, and route the next step;
- implementer: executes the quorum's next implementation task and records implementation notes;
- reviewer: reviews the implementation against the task, decision graph, and plan;
- QA: evaluates test coverage, test cases, regressions, and release risk.

Every role returns control to the quorum. The quorum decides whether to implement, fix, review, QA, validate locally, open/update the PR, inspect CI, or finish. Each loop must update the temp artifacts so the final PR has an audit trail of decisions, fixes, validation, and remaining risk.

## Workflow

Use this skill for end-to-end implementation work in this repository.

1. Set up the task.
   - If the user gives an issue number, inspect it with `gh issue view <number> --repo turboslop/deepseek-bridge`.
   - If the user gives a vague task, identify the matching issue or confirm the intended scope from local context.
   - Store coordination artifacts in a temp directory, not in the repository commit:
     ```sh
     mkdir -p /tmp/deepseek-bridge-agent
     mktemp -d /tmp/deepseek-bridge-agent/task-XXXXXX
     ```
   - Use stable filenames: `task.md`, `decision-graph.md`, `plan.md`, `implementation-log.md`, `reviewer-notes.md`, `qa-notes.md`, and `ci-notes.md`.
   - Do not store private chain-of-thought. Store concise conclusions, tradeoffs, decisions, risks, test cases, and requested changes.

2. Prepare the branch.
   - Check status with `git status --short --branch`.
   - Do not overwrite unrelated local changes.
   - Update main:
     ```sh
     git fetch origin
     git checkout main
     git merge --ff-only origin/main
     ```
   - Create a `codex/` branch, preferably `codex/issue-<number>-short-topic`.
   - If main cannot fast-forward or local changes conflict with the task, stop and report the blocker.

3. Initialize the quorum.
   - If subagents are available and allowed by the current runtime, invoke planner/architect, DevOps/platform expert, and developer experts.
   - If subagents are unavailable, perform those roles serially.
   - The quorum owns `decision-graph.md` and `plan.md`.
   - The quorum writes the next action as one of: `implement`, `review`, `qa`, `local-qg`, `open-pr`, `inspect-ci`, `finish`, or `blocked`.

4. Execute exactly one quorum action.
   - `implement`: invoke the implementer with `task.md`, `decision-graph.md`, `plan.md`, and `AGENTS.md`; update code/tests and `implementation-log.md`; then return to quorum.
   - `review`: invoke the reviewer with the task, plan, decision graph, implementation log, and diff; save bugs, regressions, missing tests, and maintainability findings in `reviewer-notes.md`; then return to quorum.
   - `qa`: invoke QA with the task, plan, decision graph, implementation log, reviewer notes, and current tests; save coverage gaps, edge cases, regression risks, and test requests in `qa-notes.md`; then return to quorum.
   - `local-qg`: run targeted checks, then full checks when shared request handling, storage, config, streaming, CLI, or CI behavior changed:
     ```sh
     uv run python -m unittest discover -s tests
     uv run pre-commit run --all-files
     uv run mypy src/ --check-untyped-defs
     ```
     Save results in `implementation-log.md`; then return to quorum.
   - `open-pr`: commit task files only, push the branch, open/update the PR against `main`, include validation results and known gaps, enable auto-merge when the PR is ready; then return to quorum.
   - `inspect-ci`: inspect PR checks and failing logs with `gh pr checks` and `gh run view --log`; save failures and proposed fixes in `ci-notes.md`; then return to quorum.
   - `finish`: confirm required checks are green and auto-merge is enabled or the PR has merged.
   - `blocked`: stop and report the blocker with the relevant temp artifact paths.

5. Let the quorum route the loop.
   - After every action, return control to the quorum.
   - The quorum reads all updated artifacts and chooses the next action.
   - Reviewer requests, QA requests, local quality gate failures, and CI failures do not directly choose the next worker; they return to quorum, and the quorum decides whether to fix, review again, QA again, validate, inspect CI, or finish.
   - Continue until the quorum chooses `finish` or `blocked`.

## Quality Gate

The repository's `main` branch should stay protected:

- require pull requests before merging;
- require branches to be up to date before merge;
- require all `CI` lint and unit-test jobs to pass;
- apply rules to administrators;
- reject force pushes and branch deletion;
- keep repository auto-merge enabled so green PRs can merge without manual clicking.

The `CI` workflow should run for pull requests and for `codex/**` branch pushes so agent branches get check runs before merge.

Do not bypass this by pushing directly to `main`. If branch protection appears disabled or required checks drift after workflow changes, restore the protection before relying on the workflow.

## Failure Handling

- If local tests fail, fix locally before opening or updating a ready PR.
- If CI fails but local tests pass, inspect platform-specific logs before guessing.
- If a failure is unrelated to the task, report it with evidence and do not hide it.
- If GitHub Actions is unavailable or queued for an unusual amount of time, leave the PR open with a clear status update.
- If auto-merge cannot be enabled because checks are pending, enable it after checks are visible or explain the blocker.
