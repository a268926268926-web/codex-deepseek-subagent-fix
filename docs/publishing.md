# Git Publication Checklist

1. Work on a dedicated branch, not `main`.
2. Run every test listed in `tests/README.md`.
3. Run `python3 scripts/check-public-tree.py .` and require `CLEAN`.
4. Review `git status --short` and `git diff --stat`.
5. Stage changes and run `git diff --cached --check`.
6. Inspect the staged file list and reject any private `.local.json`,
   generated catalog, session, database, credential, or acceptance artifact.
7. Run the relay CI workflow on the pushed branch.
8. Open a pull request or push directly only after CI and the diff review pass.

Never force-push `main`. Preserve the current local runtime bundle under
`outputs/codex-v2-relay-0162`; it is not part of the public Git tree.
