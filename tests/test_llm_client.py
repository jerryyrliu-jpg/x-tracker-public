import subprocess

import pytest

import llm_client


def test_run_text_prompt_prefers_agy_in_auto_mode(monkeypatch):
    calls = []

    def fake_which(name):
        return f"/tmp/{name}" if name in {"agy", "gemini"} else None

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(llm_client.shutil, "which", fake_which)
    monkeypatch.setattr(llm_client.subprocess, "run", fake_run)

    result = llm_client.run_text_prompt("hello", timeout=12, backend="auto")

    assert result == "ok"
    assert calls[0][0] == "agy"


def test_run_text_prompt_falls_back_to_gemini_cli_when_agy_missing(monkeypatch):
    calls = []

    def fake_which(name):
        return "/tmp/gemini" if name == "gemini" else None

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(llm_client.shutil, "which", fake_which)
    monkeypatch.setattr(llm_client.subprocess, "run", fake_run)

    result = llm_client.run_text_prompt("hello", timeout=12, backend="auto", gemini_model="gemini-2.5-flash-lite")

    assert result == "ok"
    assert calls[0][0] == "gemini"


def test_run_text_prompt_timeout_returns_empty_string(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(llm_client.shutil, "which", lambda n: f"/tmp/{n}")
    monkeypatch.setattr(llm_client.subprocess, "run", fake_run)

    result = llm_client.run_text_prompt("hello", timeout=30)
    assert result == ""


def test_run_text_prompt_os_error_returns_empty_string(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise OSError("no such file")

    monkeypatch.setattr(llm_client.shutil, "which", lambda n: f"/tmp/{n}")
    monkeypatch.setattr(llm_client.subprocess, "run", fake_run)

    result = llm_client.run_text_prompt("hello")
    assert result == ""


def test_run_text_prompt_nonzero_returncode_returns_empty_string(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="some output", stderr="error")

    monkeypatch.setattr(llm_client.shutil, "which", lambda n: f"/tmp/{n}")
    monkeypatch.setattr(llm_client.subprocess, "run", fake_run)

    result = llm_client.run_text_prompt("hello")
    assert result == ""


def test_run_text_prompt_empty_stdout_returns_empty_string(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(llm_client.shutil, "which", lambda n: f"/tmp/{n}")
    monkeypatch.setattr(llm_client.subprocess, "run", fake_run)

    result = llm_client.run_text_prompt("hello")
    assert result == ""


def test_run_text_prompt_unsupported_backend_raises_value_error(monkeypatch):
    monkeypatch.setattr(llm_client.shutil, "which", lambda n: None)
    with pytest.raises(ValueError, match="Unsupported"):
        llm_client.run_text_prompt("hello", backend="unknown_backend")


def test_run_text_prompt_explicit_agy_backend_bypasses_which(monkeypatch):
    which_calls = []

    def fake_which(name):
        which_calls.append(name)
        return None

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="result", stderr="")

    monkeypatch.setattr(llm_client.shutil, "which", fake_which)
    monkeypatch.setattr(llm_client.subprocess, "run", fake_run)

    result = llm_client.run_text_prompt("hello", backend="agy")
    assert result == "result"
    assert "agy" not in which_calls


def test_run_text_prompt_explicit_gemini_cli_backend(monkeypatch):
    seen_cmd = []

    def fake_run(cmd, **kwargs):
        seen_cmd.extend(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(llm_client.shutil, "which", lambda n: None)
    monkeypatch.setattr(llm_client.subprocess, "run", fake_run)

    result = llm_client.run_text_prompt("hello", backend="gemini-cli")
    assert result == "ok"
    assert "gemini" in seen_cmd


def test_run_text_prompt_agy_model_env_var(monkeypatch):
    seen_cmd = []

    def fake_run(cmd, **kwargs):
        seen_cmd.extend(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(llm_client.shutil, "which", lambda n: f"/tmp/{n}")
    monkeypatch.setattr(llm_client.subprocess, "run", fake_run)
    monkeypatch.setenv("AGY_MODEL", "my-custom-model")

    llm_client.run_text_prompt("hello", backend="agy")
    assert "my-custom-model" in seen_cmd
