import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.check_public_sync import (
    ALLOWED_EXACT_PATHS,
    classify_path,
    iter_candidate_paths,
    load_candidate_paths,
    main,
    run_diff_check,
    should_scan_content,
    scan_prohibited_content,
)


def test_classify_path_marks_accounts_yaml_as_forbidden():
    assert classify_path(Path("accounts.yaml")) == "forbidden"
    assert classify_path(Path("nested/accounts.yaml")) == "forbidden"


def test_classify_path_marks_forbidden_dotfiles_as_forbidden():
    assert classify_path(Path(".env")) == "forbidden"
    assert classify_path(Path(".last_guid")) == "forbidden"
    assert classify_path(Path(".last_monthly_summary")) == "forbidden"


def test_classify_path_marks_database_files_as_forbidden():
    assert classify_path(Path("tweets.db")) == "forbidden"
    assert classify_path(Path("backups/tweets.db.bak.20260623")) == "forbidden"


def test_classify_path_marks_runtime_and_cache_artifacts_as_forbidden():
    assert classify_path(Path("cpo_chain/output/report.json")) == "forbidden"
    assert classify_path(Path(".pytest_cache/v/cache/nodeids")) == "forbidden"
    assert classify_path(Path("logs/sync.log")) == "forbidden"


def test_classify_path_allows_only_exact_public_policy_paths():
    assert classify_path(Path(".gitignore")) == "allowed"
    assert classify_path(Path("tests/test_utils.py")) == "allowed"
    assert classify_path(Path("scripts/update_network_html.py")) == "allowed"
    assert classify_path(Path("cpo_chain/news_fetcher.py")) == "allowed"
    assert classify_path(Path("docs/public-sync-policy.md")) == "allowed"
    assert classify_path(Path("docs/public-release-checklist.md")) == "allowed"
    assert classify_path(Path("scripts/check_public_sync.py")) == "allowed"
    assert classify_path(Path("tests/test_check_public_sync.py")) == "allowed"


def test_classify_path_rejects_unlisted_paths_inside_allowed_directories():
    assert classify_path(Path("scripts/private_helper.py")) == "review"
    assert classify_path(Path("tests/manual_dump.py")) == "review"
    assert classify_path(Path("docs/random-note.md")) == "review"
    assert classify_path(Path("cpo_chain/private_notes.txt")) == "review"


def test_main_fails_when_candidate_list_contains_review_path(monkeypatch, capsys):
    monkeypatch.setattr(
        "scripts.check_public_sync.iter_candidate_paths",
        lambda: [Path("README.md"), Path("scripts/private_helper.py")],
    )
    monkeypatch.setattr("scripts.check_public_sync.scan_prohibited_content", lambda paths: [])
    monkeypatch.setattr("scripts.check_public_sync.run_diff_check", lambda paths=None: [])

    exit_code = main()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "review: 1" in captured.out
    assert "scripts/private_helper.py" in captured.out


def test_main_fails_when_prohibited_content_is_found(monkeypatch, capsys):
    monkeypatch.setattr(
        "scripts.check_public_sync.iter_candidate_paths",
        lambda: [Path("README.md")],
    )
    monkeypatch.setattr(
        "scripts.check_public_sync.scan_prohibited_content",
        lambda paths: ["README.md:1: prohibited content"],
    )
    monkeypatch.setattr("scripts.check_public_sync.run_diff_check", lambda paths=None: [])

    exit_code = main()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Prohibited content:" in captured.out
    assert "README.md:1: prohibited content" in captured.out


def test_main_fails_when_diff_check_reports_errors(monkeypatch, capsys):
    monkeypatch.setattr(
        "scripts.check_public_sync.iter_candidate_paths",
        lambda: [Path("README.md")],
    )
    monkeypatch.setattr("scripts.check_public_sync.scan_prohibited_content", lambda paths: [])
    monkeypatch.setattr(
        "scripts.check_public_sync.run_diff_check",
        lambda paths=None: ["README.md: trailing whitespace"],
    )

    exit_code = main()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Diff check errors:" in captured.out
    assert "README.md: trailing whitespace" in captured.out


def test_main_passes_for_clean_candidate_list(monkeypatch, capsys):
    monkeypatch.setattr(
        "scripts.check_public_sync.iter_candidate_paths",
        lambda: [Path("README.md"), Path("docs/public-sync-policy.md")],
    )
    monkeypatch.setattr("scripts.check_public_sync.scan_prohibited_content", lambda paths: [])
    monkeypatch.setattr("scripts.check_public_sync.run_diff_check", lambda paths=None: [])

    exit_code = main()

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "forbidden: 0" in captured.out
    assert "review: 0" in captured.out


def test_iter_candidate_paths_uses_public_diff(monkeypatch):
    calls = []

    class Result:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(cmd, check, capture_output, text):
        calls.append(cmd)
        if cmd[:3] == ["git", "merge-base", "public/main"]:
            return Result("abc123\n")
        return Result("README.md\nscripts/update_network_html.py\n")

    monkeypatch.setattr("scripts.check_public_sync.subprocess.run", fake_run)

    paths = iter_candidate_paths()

    assert calls == [
        ["git", "merge-base", "public/main", "HEAD"],
        ["git", "diff", "--name-only", "abc123"],
    ]
    assert paths == [Path("README.md"), Path("scripts/update_network_html.py")]


def test_run_diff_check_uses_merge_base_and_optional_paths(monkeypatch):
    calls = []

    class Result:
        stdout = ""
        stderr = ""

    def fake_run(cmd, check, capture_output, text):
        calls.append(cmd)
        if cmd[:3] == ["git", "merge-base", "public/main"]:
            return type("MergeBaseResult", (), {"stdout": "base456\n"})()
        return Result()

    monkeypatch.setattr("scripts.check_public_sync.subprocess.run", fake_run)

    errors = run_diff_check([Path("README.md"), Path("docs/public-sync-policy.md")])

    assert errors == []
    assert calls == [
        ["git", "merge-base", "public/main", "HEAD"],
        [
            "git",
            "diff",
            "--check",
            "base456",
            "--",
            "README.md",
            "docs/public-sync-policy.md",
        ],
    ]


def test_run_diff_check_skips_exempt_vendor_paths(monkeypatch):
    calls = []

    def fake_run(cmd, check, capture_output, text):
        calls.append(cmd)
        return type("Result", (), {"stdout": "", "stderr": ""})()

    monkeypatch.setattr("scripts.check_public_sync.subprocess.run", fake_run)

    errors = run_diff_check([Path("lib/vis-9.1.2/vis-network.min.js")])

    assert errors == []
    assert calls == []


def test_load_candidate_paths_reads_manifest_file(tmp_path):
    manifest = tmp_path / "publish.txt"
    manifest.write_text("README.md\nscripts/check_public_sync.py\n", encoding="utf-8")

    assert load_candidate_paths(manifest) == [Path("README.md"), Path("scripts/check_public_sync.py")]


def test_allowlist_stays_in_sync_with_public_sync_policy():
    policy_text = Path("docs/public-sync-policy.md").read_text(encoding="utf-8")
    allowed_section = policy_text.split("## Allowed Content", 1)[1].split("## Sanitized Content", 1)[0]
    policy_paths = {
        line.strip()[3:-1]
        for line in allowed_section.splitlines()
        if line.strip().startswith("- `")
    }

    assert ALLOWED_EXACT_PATHS == policy_paths


def test_policy_doc_is_not_scanned_for_literal_detection_rules():
    assert should_scan_content(Path("docs/public-sync-policy.md")) is False
    assert should_scan_content(Path("docs/public-release-checklist.md")) is True


def test_scan_prohibited_content_allows_placeholders_and_flags_real_secrets(tmp_path, monkeypatch):
    placeholder = tmp_path / "README.md"
    placeholder.write_text(
        'GEMINI_API_KEY=...\n'
        'EDGAR_USER_AGENT=x-tracker your@email.com\n'
        'export DISCORD_WEBHOOK_SAMPLE="https://discord.com/api/webhooks/..."\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("scripts.check_public_sync.should_scan_content", lambda path: True)

    assert scan_prohibited_content([placeholder]) == []

    secret_file = tmp_path / "secret.md"
    secret_file.write_text(
        "webhook https://discord.com/api/webhooks/123456789/realTokenValue\n"
        "path /Users/yj/private/file.txt\n",
        encoding="utf-8",
    )

    findings = scan_prohibited_content([secret_file])
    assert len(findings) == 2


def test_main_uses_manifest_when_provided(monkeypatch, tmp_path, capsys):
    manifest = tmp_path / "publish.txt"
    manifest.write_text("README.md\ndocs/public-sync-policy.md\n", encoding="utf-8")
    monkeypatch.setattr("scripts.check_public_sync.scan_prohibited_content", lambda paths: [])
    diff_check_calls = []

    def fake_run_diff_check(paths=None):
        diff_check_calls.append(paths)
        return []

    monkeypatch.setattr("scripts.check_public_sync.run_diff_check", fake_run_diff_check)

    exit_code = main(["--paths-file", str(manifest)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "allowed: 2" in captured.out
    assert diff_check_calls == [[Path("README.md"), Path("docs/public-sync-policy.md")]]
