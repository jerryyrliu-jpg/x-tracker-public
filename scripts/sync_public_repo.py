from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def load_manifest(path: Path) -> list[Path]:
    return [Path(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def collect_existing_files(root: Path) -> set[Path]:
    files: set[Path] = set()
    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue
        if ".git" in file_path.parts:
            continue
        files.add(file_path.relative_to(root))
    return files


def sync_manifest_files(source_root: Path, target_root: Path, manifest_paths: list[Path]) -> None:
    target_root.mkdir(parents=True, exist_ok=True)
    desired = set(manifest_paths)

    for rel_path in collect_existing_files(target_root):
        if rel_path in desired:
            continue
        (target_root / rel_path).unlink()

    for rel_path in manifest_paths:
        source = source_root / rel_path
        target = target_root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    for directory in sorted(target_root.rglob("*"), reverse=True):
        if not directory.is_dir():
            continue
        if ".git" in directory.parts:
            continue
        try:
            directory.rmdir()
        except OSError:
            continue


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    return parser.parse_args(argv if argv is not None else [])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest_paths = load_manifest(args.manifest)
    sync_manifest_files(
        source_root=args.source_root.resolve(),
        target_root=args.target_root.resolve(),
        manifest_paths=manifest_paths,
    )
    print(f"Synced {len(manifest_paths)} files into {args.target_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
