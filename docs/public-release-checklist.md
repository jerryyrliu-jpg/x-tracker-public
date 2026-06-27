# Public Release Checklist

Use this checklist before pushing any change set to `jerryyrliu-jpg/x-tracker-public`.

## Preflight

1. Run `python3 scripts/prepare_public_sync.py --write-manifest /tmp/xtracker-public-manifest.txt`.
2. Review `Blocked candidate paths` as the internal-only diff list that must stay out of the public repo.
3. Run `python3 scripts/check_public_sync.py --paths-file /tmp/xtracker-public-manifest.txt`.
4. Confirm the manifest checker exits with code `0`.
5. If the checker reports `review` or `forbidden` paths, stop and fix the manifest candidate set first.
6. If the checker reports prohibited content findings, sanitize the affected files before continuing.
7. If the checker reports diff-check errors, fix them before continuing.
8. Run `python3 scripts/sync_public_repo.py --manifest /tmp/xtracker-public-manifest.txt --target-root /path/to/x-tracker-public-clone` when you are ready to stage the public repo contents.
9. Or run `scripts/release_public.sh --target-root /path/to/x-tracker-public-clone` to execute preflight, sync, smoke tests, and final `git status` in one step.

## Required Files

1. Confirm `accounts.example.yaml` exists and is up to date.
2. Confirm `docs/public-sync-policy.md` is present when public-sync rules change.
3. Confirm `CHANGELOG.md` is updated for the public release when user-visible behavior changes.
4. Confirm any newly intended public doc path is added to the allowlist in `docs/public-sync-policy.md` before publication.

## Candidate Review

1. Open `/tmp/xtracker-public-manifest.txt`.
2. Confirm every manifest path is explicitly allowlisted in `docs/public-sync-policy.md`.
3. Review the actual diff for only those manifest paths before publication.
4. Treat any non-manifest internal docs, themes, local state files, or caches as non-public by default.

## Approval Gate

1. Present the exact candidate publication path list to the repo owner.
2. Wait for explicit repo-owner approval in the current review step or thread.
3. Do not push to `public` before that approval exists.
