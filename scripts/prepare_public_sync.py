from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

POLICY_PATH = Path("docs/public-sync-policy.md")


def extract_allowlist(policy_text: str) -> set[str]:
    allowed_section = policy_text.split("## Allowed Content", 1)[1].split("## Sanitized Content", 1)[0]
    return {
        line.strip()[3:-1]
        for line in allowed_section.splitlines()
        if line.strip().startswith("- `")
    }


def load_allowlist(policy_path: Path = POLICY_PATH) -> set[str]:
    return extract_allowlist(policy_path.read_text(encoding="utf-8"))


def get_public_merge_base() -> str:
    result = subprocess.run(
        ["git", "merge-base", "public/main", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def iter_candidate_paths() -> list[str]:
    merge_base = get_public_merge_base()
    diff_result = subprocess.run(
        ["git", "diff", "--name-only", merge_base],
        check=True,
        capture_output=True,
        text=True,
    )
    untracked_result = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
        text=True,
    )
    return sorted(
        {line for line in (diff_result.stdout.splitlines() + untracked_result.stdout.splitlines()) if line}
    )


def filter_candidate_paths(candidates: list[str], allowlist: set[str]) -> tuple[list[str], list[str]]:
    allowed: list[str] = []
    blocked: list[str] = []
    for path in candidates:
        if path in allowlist:
            allowed.append(path)
        else:
            blocked.append(path)
    return allowed, blocked


def format_report(allowed: list[str], blocked: list[str]) -> str:
    lines = ["Allowed candidate paths:"]
    if allowed:
        lines.extend(f"- {path}" for path in allowed)
    else:
        lines.append("- (none)")

    lines.append("")
    lines.append("Blocked candidate paths:")
    if blocked:
        lines.extend(f"- {path}" for path in blocked)
    else:
        lines.append("- (none)")

    return "\n".join(lines)


def write_manifest(path: Path, allowed: list[str]) -> None:
    contents = "".join(f"{candidate}\n" for candidate in allowed)
    path.write_text(contents, encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-manifest", type=Path)
    return parser.parse_args(argv if argv is not None else [])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    allowlist = load_allowlist()
    candidates = iter_candidate_paths()
    allowed, blocked = filter_candidate_paths(candidates, allowlist)
    print(format_report(allowed, blocked))
    if args.write_manifest is not None:
        write_manifest(args.write_manifest, allowed)
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
