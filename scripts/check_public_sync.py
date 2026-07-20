from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ALLOWED_EXACT_PATHS = {
    ".gitignore",
    "CHANGELOG.md",
    "README.md",
    "accounts.example.yaml",
    "config.env.example",
    "conftest.py",
    "dashboard.py",
    "discord_bot.py",
    "graph_builder.py",
    "llm_client.py",
    "llm_url.py",
    "monitor_active.py",
    "monitor_rss.py",
    "monthly_summary.py",
    "pytest.ini",
    "query_topic.py",
    "requirements.txt",
    "scraper.py",
    "scraper_playwright.py",
    "utils.py",
    "scripts/backfill_confidence.py",
    "scripts/backfill_tweet_images.py",
    "scripts/fix_cpo_tickers.py",
    "scripts/import_cpo_chain.py",
    "scripts/migrate_v2.py",
    "scripts/prepare_public_sync.py",
    "scripts/release_public.sh",
    "scripts/restart_chrome.sh",
    "scripts/run_news_discovery.py",
    "scripts/update_network_html.py",
    "cpo_chain/__init__.py",
    "cpo_chain/batch_embed.py",
    "cpo_chain/company_ticker_mapper.py",
    "cpo_chain/confidence_updater.py",
    "cpo_chain/db.py",
    "cpo_chain/edgar_fetcher.py",
    "cpo_chain/embedder.py",
    "cpo_chain/entity_resolver.py",
    "cpo_chain/export_universal.py",
    "cpo_chain/extract_universal.py",
    "cpo_chain/keywords.yaml",
    "cpo_chain/news_article_fetcher.py",
    "cpo_chain/news_extractor.py",
    "cpo_chain/news_fetcher.py",
    "cpo_chain/normalization.py",
    "cpo_chain/ocr_utils.py",
    "cpo_chain/prompts.py",
    "cpo_chain/vec_db.py",
    "docs/public-release-checklist.md",
    "docs/public-sync-policy.md",
    "lib/bindings/utils.js",
    "lib/tom-select/tom-select.complete.min.js",
    "lib/tom-select/tom-select.css",
    "lib/vis-9.1.2/vis-network.css",
    "lib/vis-9.1.2/vis-network.min.js",
    "scripts/check_public_sync.py",
    "scripts/sync_public_repo.py",
    "tests/__init__.py",
    "tests/test_check_public_sync.py",
    "tests/repro_supply_bug.py",
    "tests/test_cache_key.py",
    "tests/test_confidence_updater.py",
    "tests/test_db.py",
    "tests/test_discord_bot.py",
    "tests/test_edgar_fetcher.py",
    "tests/test_entity_resolver.py",
    "tests/test_export_universal.py",
    "tests/test_extract_universal.py",
    "tests/test_llm_client.py",
    "tests/test_llm_url.py",
    "tests/test_news_article_fetcher.py",
    "tests/test_news_extractor.py",
    "tests/test_news_fetcher.py",
    "tests/test_normalization.py",
    "tests/test_paths.py",
    "tests/test_prepare_public_sync.py",
    "tests/test_prompt.py",
    "tests/test_release_public_script.py",
    "tests/test_scraper_playwright.py",
    "tests/test_slash_commands.py",
    "tests/test_summary.py",
    "tests/test_sync_public_repo.py",
    "tests/test_update_network_html.py",
    "tests/test_utils.py",
}

FORBIDDEN_DIR_NAMES = {
    ".gemini",
    ".mypy_cache",
    ".profiles",
    ".pytest_cache",
    ".ruff_cache",
    ".superpowers",
    ".worktrees",
    "__pycache__",
    "logs",
    "node_modules",
    "venv",
}

FORBIDDEN_EXACT_PATHS = {
    ".env",
    ".last_guid",
    ".last_monthly_summary",
    "accounts.yaml",
    "graph.html",
    "metrics.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "temp_acc.txt",
    "yarn.lock",
}

FORBIDDEN_PATH_PREFIXES = ("cpo_chain/output/",)
FORBIDDEN_SUFFIXES = {".db", ".log", ".lock", ".pyc", ".pyo"}
CONTENT_SCAN_PATHS = {"README.md", "config.env.example", "accounts.example.yaml"}
CONTENT_SCAN_EXCLUDED_PATHS = {"docs/public-sync-policy.md"}
DIFF_CHECK_EXCLUDED_PATHS = {"lib/vis-9.1.2/vis-network.min.js"}
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"xox[baprs]-[0-9A-Za-z-]+"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"https://discord\.com/api/webhooks/\d+/[A-Za-z0-9._-]+"),
)
ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"/Users/"),
    re.compile(r"/private/"),
    re.compile(r"/home/"),
)
TIMESTAMP_PATTERNS = (
    re.compile(r"\b20\d\d-\d\d-\d\dT"),
    re.compile(r"Generated at:\s*20\d\d-\d\d-\d\d \d\d:\d\d:\d\d"),
)
PLACEHOLDER_EMAILS = {"your@email.com", "contact@example.com"}
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def normalize_path(path: Path) -> str:
    normalized = path.as_posix()
    if normalized.startswith("./"):
        return normalized[2:]
    return normalized


def get_public_merge_base() -> str:
    result = subprocess.run(
        ["git", "merge-base", "public/main", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def classify_path(path: Path) -> str:
    normalized = normalize_path(path)
    normalized_path = Path(normalized)
    parts = set(normalized_path.parts)

    if normalized in FORBIDDEN_EXACT_PATHS or normalized_path.name == "accounts.yaml":
        return "forbidden"

    if any(normalized.startswith(prefix) for prefix in FORBIDDEN_PATH_PREFIXES):
        return "forbidden"

    if parts & FORBIDDEN_DIR_NAMES:
        return "forbidden"

    if normalized_path.suffix in FORBIDDEN_SUFFIXES:
        return "forbidden"

    if ".db.bak." in normalized:
        return "forbidden"

    if normalized in ALLOWED_EXACT_PATHS:
        return "allowed"

    return "review"


def iter_candidate_paths() -> list[Path]:
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
    candidates = {
        Path(line)
        for line in (diff_result.stdout.splitlines() + untracked_result.stdout.splitlines())
        if line
    }
    return sorted(candidates, key=lambda path: path.as_posix())


def load_candidate_paths(path: Path) -> list[Path]:
    return [Path(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def should_scan_content(path: Path) -> bool:
    normalized = normalize_path(path)
    if normalized in CONTENT_SCAN_EXCLUDED_PATHS:
        return False
    return normalized in CONTENT_SCAN_PATHS or (
        normalized in ALLOWED_EXACT_PATHS and normalized.startswith("docs/")
    )


def line_has_prohibited_content(line: str) -> bool:
    for pattern in SECRET_PATTERNS:
        if pattern.search(line):
            return True

    for pattern in ABSOLUTE_PATH_PATTERNS:
        if pattern.search(line):
            return True

    for pattern in TIMESTAMP_PATTERNS:
        if pattern.search(line):
            return True

    for match in EMAIL_RE.finditer(line):
        email = match.group(0)
        if email not in PLACEHOLDER_EMAILS and not email.endswith("example.com"):
            return True

    return False


def scan_prohibited_content(paths: list[Path]) -> list[str]:
    findings: list[str] = []

    for path in paths:
        if not should_scan_content(path):
            continue

        file_path = Path(path)
        if not file_path.exists():
            continue

        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(f"{file_path.as_posix()}:binary-or-non-utf8")
            continue

        for lineno, line in enumerate(text.splitlines(), start=1):
            if line_has_prohibited_content(line):
                findings.append(f"{file_path.as_posix()}:{lineno}: {line.strip()}")

    return findings


def run_diff_check(paths: list[Path] | None = None) -> list[str]:
    filtered_paths = None
    if paths is not None:
        filtered_paths = [
            path for path in paths if normalize_path(path) not in DIFF_CHECK_EXCLUDED_PATHS
        ]
        if not filtered_paths:
            return []

    merge_base = get_public_merge_base()
    cmd = ["git", "diff", "--check", merge_base]
    if filtered_paths:
        cmd.extend(["--", *(normalize_path(path) for path in filtered_paths)])

    result = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
    )
    output = result.stdout.strip() or result.stderr.strip()
    if not output:
        return []
    return [line for line in output.splitlines() if line]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--paths-file", type=Path)
    return parser.parse_args(argv if argv is not None else [])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    counts: Counter[str] = Counter()
    flagged: list[tuple[str, str]] = []
    candidate_paths = load_candidate_paths(args.paths_file) if args.paths_file else iter_candidate_paths()

    for path in candidate_paths:
        status = classify_path(path)
        counts[status] += 1
        if status != "allowed":
            flagged.append((status, path.as_posix()))

    content_findings = scan_prohibited_content(candidate_paths)
    diff_errors = run_diff_check(candidate_paths if args.paths_file else None)

    for status in ("allowed", "sanitized", "review", "forbidden"):
        print(f"{status}: {counts.get(status, 0)}")

    if flagged:
        print("\nFlagged paths:")
        for status, name in flagged:
            print(f"- {status}: {name}")

    if content_findings:
        print("\nProhibited content:")
        for finding in content_findings:
            print(f"- {finding}")

    if diff_errors:
        print("\nDiff check errors:")
        for error in diff_errors:
            print(f"- {error}")

    return 1 if flagged or content_findings or diff_errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
