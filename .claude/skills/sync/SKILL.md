---
name: sync
description: Fetch origin, rebase the current branch on origin/main, run this area's tests and report conflicts.
---
1. Run `git status`. If there are uncommitted changes, stop and ask me whether to commit or stash them.
2. `git fetch origin`, then `git rebase origin/main`.
3. If the rebase hits conflicts, do not guess. Run `git rebase --abort`, then list each conflicting file,
   what changed on each side, and which area owns it. Wait for me.
4. If the rebase succeeds, run the tests for the area I'm working in:
   `uv run pytest` in core/, pipeline/ or server/, `npm test` in web/.
5. Report: commits pulled in, whether contracts/ changed (if so, summarise the change), and the test result.
Never push, and never force-push.
