import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.prepare_public_sync import (
    extract_allowlist,
    filter_candidate_paths,
    format_report,
    iter_candidate_paths,
    main,
    write_manifest,
)


def test_extract_allowlist_reads_exact_paths_only():
    policy = """# Public Sync Policy

## Allowed Content
- `README.md`
- `scripts/check_public_sync.py`
- No other files are allowed unless their exact path is added to this list in a tracked commit

## Sanitized Content
- `accounts.yaml` must be replaced with `accounts.example.yaml`
"""

    assert extract_allowlist(policy) == {"README.md", "scripts/check_public_sync.py"}


def test_filter_candidate_paths_splits_allowed_and_blocked():
    allowlist = {"README.md", "docs/public-sync-policy.md"}
    candidates = [
        "README.md",
        "docs/public-sync-policy.md",
        "themes/USCI_Report.md",
        "graph.html",
    ]

    allowed, blocked = filter_candidate_paths(candidates, allowlist)

    assert allowed == ["README.md", "docs/public-sync-policy.md"]
    assert blocked == ["themes/USCI_Report.md", "graph.html"]


def test_format_report_includes_allowed_and_blocked_sections():
    report = format_report(
        ["README.md", "docs/public-sync-policy.md"],
        ["themes/USCI_Report.md"],
    )

    assert "Allowed candidate paths:" in report
    assert "- README.md" in report
    assert "Blocked candidate paths:" in report
    assert "- themes/USCI_Report.md" in report


def test_write_manifest_writes_one_path_per_line(tmp_path):
    manifest = tmp_path / "publish.txt"

    write_manifest(manifest, ["README.md", "scripts/check_public_sync.py"])

    assert manifest.read_text(encoding="utf-8") == "README.md\nscripts/check_public_sync.py\n"


def test_iter_candidate_paths_uses_merge_base_diff(monkeypatch):
    calls = []

    class Result:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(cmd, check, capture_output, text):
        calls.append(cmd)
        if cmd[:3] == ["git", "merge-base", "public/main"]:
            return Result("base123\n")
        return Result("README.md\nscripts/check_public_sync.py\n")

    monkeypatch.setattr("scripts.prepare_public_sync.subprocess.run", fake_run)

    candidates = iter_candidate_paths()

    assert candidates == ["README.md", "scripts/check_public_sync.py"]
    assert calls == [
        ["git", "merge-base", "public/main", "HEAD"],
        ["git", "diff", "--name-only", "base123"],
    ]


def test_main_writes_manifest_and_exits_zero_when_all_candidates_allowed(monkeypatch, tmp_path, capsys):
    manifest = tmp_path / "publish.txt"
    monkeypatch.setattr(
        "scripts.prepare_public_sync.load_allowlist",
        lambda policy_path=None: {"README.md", "scripts/check_public_sync.py"},
    )
    monkeypatch.setattr(
        "scripts.prepare_public_sync.iter_candidate_paths",
        lambda: ["README.md", "scripts/check_public_sync.py"],
    )

    exit_code = main(["--write-manifest", str(manifest)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Allowed candidate paths:" in captured.out
    assert manifest.read_text(encoding="utf-8") == "README.md\nscripts/check_public_sync.py\n"


def test_main_does_not_parse_pytest_arguments(monkeypatch, capsys):
    monkeypatch.setattr(
        "scripts.prepare_public_sync.load_allowlist",
        lambda policy_path=None: {"README.md"},
    )
    monkeypatch.setattr("scripts.prepare_public_sync.iter_candidate_paths", lambda: ["README.md"])

    exit_code = main()

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Allowed candidate paths:" in captured.out
