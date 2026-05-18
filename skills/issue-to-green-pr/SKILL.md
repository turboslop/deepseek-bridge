---
name: issue-to-green-pr
description: Multi-agent repository workflow for taking a GitHub issue or implementation task in turboslop/deepseek-bridge from current main through planning quorum, branch creation, implementation, local validation, review, QA, pull request creation, CI monitoring, iterative fixes, and auto-merge. Use when asked to pick up an issue, work an issue, implement a GitHub task, create a PR, get checks green, or follow the repo's standard task-to-PR flow.
---

# Issue To Green PR

## Local Gang Of Four

Run this workflow as a local Gang of Four:

- planning quorum: planner/architect, DevOps/platform expert, and developer discuss the solution space and produce one decision graph plus one plan;
- implementer: executes the plan and records implementation notes;
- reviewer: reviews the implementation against the task and plan;
- QA: evaluates test coverage, test cases, regressions, and release risk.

The reviewer and QA can return work to the implementer. CI failures also return work to the implementer. Each loop must update the temp artifacts so the final PR has an audit trail of decisions, fixes, validation, and remaining risk.

## Workflow

Use this skill for end-to-end implementation work in this repository.

1. Resolve the task.
   - If the user gives an issue number, inspect it with `gh issue view <number> --repo turboslop/deepseek-bridge`.
   - If the user gives a vague task, identify the matching issue or confirm the intended scope from local context.
   - Restate the implementation target in one concise sentence before changing files.

2. Create a temporary agent workspace.
   - Store coordination artifacts in a temp directory, not in the repository commit.
   - Example:
     ```sh
     mkdir -p /tmp/deepseek-bridge-agent
     mktemp -d /tmp/deepseek-bridge-agent/task-XXXXXX
     ```
   - Use stable filenames inside that directory:
     - `task.md`
     - `decision-graph.md`
     - `plan.md`
     - `implementation-log.md`
     - `reviewer-notes.md`
     - `qa-notes.md`
     - `ci-notes.md`
   - Do not store private chain-of-thought. Store concise conclusions, tradeoffs, decisions, risks, test cases, and requested changes.

3. Start from a clean, current `main`.
   - Check status with `git status --short --branch`.
   - Do not overwrite unrelated local changes.
   - Update main:
     ```sh
     git fetch origin
     git checkout main
     git merge --ff-only origin/main
     ```
   - If main cannot fast-forward, stop and report the blocker.

4. Create a task branch.
   - Use the `codex/` prefix.
   - Prefer `codex/issue-<number>-short-topic` for issue work.
   - Example:
     ```sh
     git checkout -b codex/issue-9-valkey-cache
     ```

5. Run the planning quorum.
   - If subagents are available and allowed by the current runtime, invoke a small expert quorum before implementation:
     - planner/architect: design, boundaries, rollout risks;
     - DevOps/platform expert: CI, deployment, config, observability, operational risks;
     - developer: code ownership, implementation path, tests, compatibility.
   - Give each expert the task, relevant issue, repository context, and `AGENTS.md`.
   - Ask experts to contribute to one shared `decision-graph.md` artifact: options, constraints, dependencies, disagreements, and final decisions.
   - Ask the quorum to produce one `plan.md` with implementation steps, files likely to change, tests to run, risk list, and explicit out-of-scope items.
   - If subagents are unavailable, perform the same roles serially and write the same artifacts.
   - Do not proceed if the quorum identifies unclear requirements that materially change scope; ask the user or update the issue first.

6. Invoke the implementer.
   - The implementer reads `task.md`, `decision-graph.md`, `plan.md`, and `AGENTS.md`.
   - Implement narrowly.
   - Follow `AGENTS.md`.
   - Preserve existing local CLI/TUI behavior unless the issue explicitly changes it.
   - Add or update tests for changed behavior.
   - Keep commits focused on the issue.
   - Update `implementation-log.md` with changed files, key decisions, local validation, and known risks.

7. Validate local quality gates.
   - Run targeted tests first while iterating.
   - For shared request handling, storage, config, streaming, or CLI changes, run the full suite:
     ```sh
     uv run python -m unittest discover -s tests
     ```
   - Run applicable lint/type checks:
     ```sh
     uv run pre-commit run --all-files
     uv run mypy src/ --check-untyped-defs
     ```
   - If a required tool is unavailable, document the exact command that could not run and why.

8. Invoke the reviewer.
   - Give the reviewer the task, `plan.md`, `decision-graph.md`, `implementation-log.md`, and the diff.
   - Ask for a code-review stance: bugs, regressions, API compatibility issues, missing tests, security/logging risks, and maintainability issues.
   - Save findings in `reviewer-notes.md`.
   - If the reviewer requests changes, return to the implementer, apply fixes, rerun relevant local quality gates, and update `implementation-log.md`.

9. Invoke QA.
   - Give QA the task, `plan.md`, `decision-graph.md`, `implementation-log.md`, reviewer notes, and current tests.
   - Ask QA to evaluate test coverage, edge cases, regression risk, platform coverage, and missing test cases.
   - Save findings in `qa-notes.md`.
   - If QA requests changes, return to the implementer, apply fixes, rerun relevant local quality gates, and update `implementation-log.md`.
   - Repeat reviewer/QA loops until there are no blocking findings, or stop with a clear blocker.

10. Commit and push.
   - Review the diff before staging.
   - Stage only files belonging to the task.
   - Use a clear commit message.
   - Push the branch:
     ```sh
     git push -u origin <branch>
     ```

11. Open a pull request.
   - Create a PR against `main`.
   - Include:
     - issue link, if applicable;
     - concise change summary;
     - local validation commands and results;
     - known gaps, if any.
   - Prefer draft PRs only when the user asks for an early checkpoint. Otherwise create a ready PR.

12. Enable auto-merge when appropriate.
   - The repository is configured to require PR checks on `main`.
   - Enable auto-merge only after the PR is ready and local validation has passed.
   - Use the repo's preferred merge style unless the user asks otherwise.

13. Watch CI until it is green.
   - Use:
     ```sh
     gh pr checks <pr-number> --repo turboslop/deepseek-bridge --watch
     ```
   - Required checks currently come from the `CI` workflow:
     - `lint`
     - `Unit test (ubuntu-latest, Python 3.10)`
     - `Unit test (ubuntu-latest, Python 3.11)`
     - `Unit test (ubuntu-latest, Python 3.12)`
     - `Unit test (ubuntu-latest, Python 3.13)`
     - `Unit test (macos-latest, Python 3.13)`
     - `Unit test (windows-latest, Python 3.13)`

14. Fix failures until green.
    - If a check fails, inspect logs with `gh pr checks` and `gh run view --log`.
    - Save failure analysis and action items in `ci-notes.md`.
    - Return to the implementer with `ci-notes.md`, failing logs, and the current diff.
    - Make the smallest fix that addresses the failure.
    - Run the relevant local test or lint command.
    - Commit and push again.
    - For substantive fixes, rerun the reviewer and QA loop before waiting on CI again.
    - Continue until required checks pass or a real external blocker is identified.

15. Finish only when the PR is mergeable.
    - Confirm required checks are passing.
    - Confirm auto-merge is enabled or the PR has merged.
    - If auto-merge merged the PR, fetch and update local main:
      ```sh
      git fetch origin
      git checkout main
      git merge --ff-only origin/main
      ```

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
