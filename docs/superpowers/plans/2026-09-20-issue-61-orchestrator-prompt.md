# Orchestrator brief: issue #61 search correctness

You are the orchestrator for the flackey epic "Search correctness" (GitHub issue #61 in EyalDelarea/flackey). Repo: `/Users/delarea/Desktop/code/cratedigger`. You orchestrate; you do not implement.

## Read first, in this order

1. `CLAUDE.md` at the repo root.
2. `docs/superpowers/specs/2026-09-20-issue-61-search-correctness-design.md`: the decisions.
3. `docs/superpowers/plans/2026-09-20-issue-61-search-correctness.md`: 17 tasks (0 to 16) with code and tests. The plan is authoritative on behaviour; the repo is authoritative on names. When they disagree, the implementer keeps the plan's behaviour, uses the repo's names, and notes the mismatch in the PR body.
4. `docs/audit-issue-61.md` and `docs/search-quality-review-61.md`, skimmed: the evidence behind the decisions.

## How to run it

- Use the `superpowers:subagent-driven-development` skill: one fresh implementer subagent per task, then a reviewer subagent, then you decide. Never two tasks in flight that both touch `worker.py`.
- Models: implementers on Opus; reviewers and mechanical work (Task 0 issue edits, Task 1 and Task 16 scripts, doc commits) on Sonnet. You make no code edits yourself beyond a one-line fix a reviewer already specified.
- Task order is strict: 0, 1, 2, 3, 4, 5+6 (one PR), 7, 8 to 11 (one PR), 12, 13, 14, 15, 16. Phase 5 (Tasks 8 to 11) starts only after Task 7's reports are committed. Phase 6 (12 to 14) starts only after the #68 PR is merged.
- Give each implementer: the task's full text copied from the plan, the plan's Global Constraints section, the spec path, the branch to base on, and the plan's "Facts checked against the code" paragraph. Nothing else from this brief.
- Give each reviewer: the diff (`gh pr diff <n>`), the task text, and one question: does this do what the task says, with the tests the task lists, and nothing the task did not ask for? Not your own opinion of the diff.

## Branching and PRs

- Everything lands on `epic/61-search-correctness`, cut from `main` in Task 0. Every task PR: base `epic/61-search-correctness`, labelled before opening (`bug`, `enhancement`, `chore` or `ignore-for-release`), squash-merged after a clean review and green CI. Never squash the epic itself; the owner merges it into `main` with a merge commit at the end so one task can be reverted alone.
- CI gate before every PR, all five green: `uv run ruff check src tests`, `uv run lint-imports`, `uv run pytest -q`, `npm --prefix web test -- --run`, `npm --prefix web run build`.
- If an implementer works in a git worktree, tests need `PYTHONPATH=<worktree>/src` or pytest imports the main checkout's `flackey`.
- Commit messages and PR bodies end with the attribution lines your session's system reminder gives you.

## Stop and ask the owner, do not proceed on an assumption

1. Task 1: before each rename or delete of a library file, name the path and wait for a yes.
2. Task 7: if the fingerprint gap is under 0.10, or any certified-correct track's record score is below the chosen threshold.
3. Task 16: before re-queueing anything.

Everything else: decide and continue. Report at each of these points and after each phase, in five lines or fewer: what merged, what the sweep or replay said, what is next. The owner wants short, plain messages.

## Never

- No Soulseek downloads during measurement (Tasks 5 to 7). YouTube audio and Deezer previews are fine.
- No changes to the live database except through `flackey.store.Store` in Tasks 1 and 16, with the app quit.
- No scope beyond the plan: no artist floor, no junk-candidate gate, no unknown-length rule, no per-reference duration tolerance, no new review kind, no EP handling, no Spotify audio identification, not #53. If an implementer proposes one, decline it and note it in the final report.
- Do not rewrite the plan. If a task cannot be done as written, stop, say why in three sentences, and propose the smallest change.

## Done means

Task 16's report committed, its summary posted on #61, and the epic PR open against `main` listing every task PR. Then hand back to the owner for the A/B test and the merge.
