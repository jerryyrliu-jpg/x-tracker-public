import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.sync_public_repo import collect_existing_files, sync_manifest_files


def test_collect_existing_files_skips_git_directory(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("ignored", encoding="utf-8")
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("guide", encoding="utf-8")

    files = collect_existing_files(tmp_path)

    assert files == {Path("README.md"), Path("docs/guide.md")}


def test_sync_manifest_files_copies_manifest_and_removes_extras(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()

    (source / "README.md").write_text("public readme", encoding="utf-8")
    (source / "docs").mkdir()
    (source / "docs" / "policy.md").write_text("policy", encoding="utf-8")

    (target / ".git").mkdir()
    (target / ".git" / "config").write_text("keep", encoding="utf-8")
    (target / "old.txt").write_text("remove", encoding="utf-8")
    (target / "docs").mkdir()
    (target / "docs" / "stale.md").write_text("remove", encoding="utf-8")

    sync_manifest_files(
        source_root=source,
        target_root=target,
        manifest_paths=[Path("README.md"), Path("docs/policy.md")],
    )

    assert (target / "README.md").read_text(encoding="utf-8") == "public readme"
    assert (target / "docs" / "policy.md").read_text(encoding="utf-8") == "policy"
    assert not (target / "old.txt").exists()
    assert not (target / "docs" / "stale.md").exists()
    assert (target / ".git" / "config").read_text(encoding="utf-8") == "keep"
