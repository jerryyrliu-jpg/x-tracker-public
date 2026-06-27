# Changelog

All notable public-facing changes to this repository will be documented in this file.

## 2026-06-27

### Public Sync Workflow
- Added `scripts/check_public_sync.py` to validate public publication candidates against an explicit allowlist.
- Added `scripts/prepare_public_sync.py` to generate a manifest for public-safe publication paths.
- Added `scripts/sync_public_repo.py` to copy only manifest-approved files into a clean public repository checkout.
- Added `docs/public-sync-policy.md` and `docs/public-release-checklist.md` to document the release gate and publication policy.
- Replaced tracked `accounts.yaml` with `accounts.example.yaml` for public-safe setup instructions.

### Documentation
- Updated `README.md` with the public preflight workflow and safer setup guidance.
- Added this public-facing changelog so releases remain understandable after private-only files are excluded.
