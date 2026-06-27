#!/bin/sh

set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
MANIFEST_PATH="/tmp/xtracker-public-manifest.txt"
TARGET_ROOT=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --target-root)
      TARGET_ROOT="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [ -z "$TARGET_ROOT" ]; then
  echo "--target-root is required" >&2
  exit 2
fi

if ! python3 "$REPO_ROOT/scripts/prepare_public_sync.py" --write-manifest "$MANIFEST_PATH"; then
  echo "prepare_public_sync.py reported blocked internal-only paths; continuing with manifest validation" >&2
fi
python3 "$REPO_ROOT/scripts/check_public_sync.py" --paths-file "$MANIFEST_PATH"
python3 "$REPO_ROOT/scripts/sync_public_repo.py" \
  --source-root "$REPO_ROOT" \
  --manifest "$MANIFEST_PATH" \
  --target-root "$TARGET_ROOT"

(
  cd "$TARGET_ROOT"
  pytest -q tests/test_check_public_sync.py tests/test_prepare_public_sync.py tests/test_sync_public_repo.py
)

git -C "$TARGET_ROOT" status --short
