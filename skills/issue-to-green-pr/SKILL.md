---
name: issue-to-green-pr
description: Repository workflow for taking a GitHub issue or implementation task in turboslop/deepseek-bridge from current main through branch creation, implementation, local validation, pull request creation, CI monitoring, iterative fixes, and auto-merge. Use when asked to pick up an issue, work an issue, implement a GitHub task, create a PR, get checks green, or follow the repo's standard task-to-PR flow.
---

# Issue To Green PR

## Workflow

Use this skill for end-to-end implementation work in this repository.

1. Resolve the task.
   - If the user gives an issue number, inspect it with `gh issue view <number> --repo turboslop/deepseek-bridge`.
   - If the user gives a vague task, identify the matching issue or confirm the intended scope from local context.
   - Restate the implementation target in one concise sentence before changing files.

2. Start from a clean, current `main`.
   - Check status with `git status --short --branch`.
   - Do not overwrite unrelated local changes.
   - Update main:
     ```sh
     git fetch origin
     git checkout main
     git merge --ff-only origin/main
     ```
   - If main cannot fast-forward, stop and report the blocker.

3. Create a task branch.
   - Use the `codex/` prefix.
   - Prefer `codex/issue-<number>-short-topic` for issue work.
   - Example:
     ```sh
     git checkout -b codex/issue-9-valkey-cache
     ```

4. Implement narrowly.
   - Follow `AGENTS.md`.
   - Preserve existing local CLI/TUI behavior unless the issue explicitly changes it.
   - Add or update tests for changed behavior.
   - Keep commits focused on the issue.

5. Validate locally.
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

6. Commit and push.
   - Review the diff before staging.
   - Stage only files belonging to the task.
   - Use a clear commit message.
   - Push the branch:
     ```sh
     git push -u origin <branch>
     ```

7. Open a pull request.
   - Create a PR against `main`.
   - Include:
     - issue link, if applicable;
     - concise change summary;
     - local validation commands and results;
     - known gaps, if any.
   - Prefer draft PRs only when the user asks for an early checkpoint. Otherwise create a ready PR.

8. Enable auto-merge when appropriate.
   - The repository is configured to require PR checks on `main`.
   - Enable auto-merge only after the PR is ready and local validation has passed.
   - Use the repo's preferred merge style unless the user asks otherwise.

9. Watch CI until it is green.
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

10. Fix failures until green.
    - If a check fails, inspect logs with `gh pr checks` and `gh run view --log`.
    - Make the smallest fix that addresses the failure.
    - Run the relevant local test or lint command.
    - Commit and push again.
    - Continue until required checks pass or a real external blocker is identified.

11. Finish only when the PR is mergeable.
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

Do not bypass this by pushing directly to `main`. If branch protection appears disabled or required checks drift after workflow changes, restore the protection before relying on the workflow.

## Failure Handling

- If local tests fail, fix locally before opening or updating a ready PR.
- If CI fails but local tests pass, inspect platform-specific logs before guessing.
- If a failure is unrelated to the task, report it with evidence and do not hide it.
- If GitHub Actions is unavailable or queued for an unusual amount of time, leave the PR open with a clear status update.
- If auto-merge cannot be enabled because checks are pending, enable it after checks are visible or explain the blocker.
