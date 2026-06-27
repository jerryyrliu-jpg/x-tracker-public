import os
import stat
import subprocess
from pathlib import Path


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_release_public_script_runs_expected_steps(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_path = tmp_path / "calls.log"
    target_root = tmp_path / "public-clone"
    target_root.mkdir()

    _write_executable(
        fake_bin / "python3",
        "\n".join(
            [
                "#!/bin/sh",
                f'echo "python3:$*" >> "{log_path}"',
            ]
        )
        + "\n",
    )
    _write_executable(
        fake_bin / "pytest",
        "\n".join(
            [
                "#!/bin/sh",
                f'echo "pytest:$*" >> "{log_path}"',
            ]
        )
        + "\n",
    )
    _write_executable(
        fake_bin / "git",
        "\n".join(
            [
                "#!/bin/sh",
                f'echo "git:$*" >> "{log_path}"',
            ]
        )
        + "\n",
    )

    script_path = Path("scripts/release_public.sh").resolve()
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    subprocess.run(
        ["bash", str(script_path), "--target-root", str(target_root)],
        check=True,
        cwd=Path.cwd(),
        env=env,
    )

    calls = log_path.read_text(encoding="utf-8").splitlines()
    assert calls == [
        "python3:/Users/yj/projects/x-tracker/scripts/prepare_public_sync.py --write-manifest /tmp/xtracker-public-manifest.txt",
        "python3:/Users/yj/projects/x-tracker/scripts/check_public_sync.py --paths-file /tmp/xtracker-public-manifest.txt",
        f"python3:/Users/yj/projects/x-tracker/scripts/sync_public_repo.py --source-root /Users/yj/projects/x-tracker --manifest /tmp/xtracker-public-manifest.txt --target-root {target_root}",
        "pytest:-q tests/test_check_public_sync.py tests/test_prepare_public_sync.py tests/test_sync_public_repo.py",
        f"git:-C {target_root} status --short",
    ]


def test_release_public_script_continues_when_prepare_returns_warning(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_path = tmp_path / "calls.log"
    target_root = tmp_path / "public-clone"
    target_root.mkdir()

    _write_executable(
        fake_bin / "python3",
        "\n".join(
            [
                "#!/bin/sh",
                f'echo "python3:$*" >> "{log_path}"',
                'case "$1" in',
                '  */prepare_public_sync.py) exit 1 ;;',
                "esac",
            ]
        )
        + "\n",
    )
    _write_executable(
        fake_bin / "pytest",
        "\n".join(
            [
                "#!/bin/sh",
                f'echo "pytest:$*" >> "{log_path}"',
            ]
        )
        + "\n",
    )
    _write_executable(
        fake_bin / "git",
        "\n".join(
            [
                "#!/bin/sh",
                f'echo "git:$*" >> "{log_path}"',
            ]
        )
        + "\n",
    )

    script_path = Path("scripts/release_public.sh").resolve()
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    subprocess.run(
        ["bash", str(script_path), "--target-root", str(target_root)],
        check=True,
        cwd=Path.cwd(),
        env=env,
    )

    calls = log_path.read_text(encoding="utf-8").splitlines()
    assert calls[1:] == [
        "python3:/Users/yj/projects/x-tracker/scripts/check_public_sync.py --paths-file /tmp/xtracker-public-manifest.txt",
        f"python3:/Users/yj/projects/x-tracker/scripts/sync_public_repo.py --source-root /Users/yj/projects/x-tracker --manifest /tmp/xtracker-public-manifest.txt --target-root {target_root}",
        "pytest:-q tests/test_check_public_sync.py tests/test_prepare_public_sync.py tests/test_sync_public_repo.py",
        f"git:-C {target_root} status --short",
    ]
