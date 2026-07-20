import subprocess

import pytest

import llm_client


def _fake_run_ok(cmd, **kwargs):
    return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")


def _fake_run_fail(cmd, **kwargs):
    return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="agy failed")


def test_run_text_prompt_auto_uses_agy(monkeypatch):
    calls = []
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(llm_client.subprocess, "run", fake_run)

    result = llm_client.run_text_prompt("hello", timeout=12, backend="auto")

    assert result == "ok"
    assert calls[0][0] == "agy"


def test_run_text_prompt_auto_agy_fail_returns_empty(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(llm_client.subprocess, "run", _fake_run_fail)

    result = llm_client.run_text_prompt("hello", timeout=12, backend="auto")

    assert result == ""


def test_run_text_prompt_timeout_returns_empty_string(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr(llm_client.subprocess, "run", fake_run)

    result = llm_client.run_text_prompt("hello", timeout=30)
    assert result == ""


def test_run_text_prompt_os_error_returns_empty_string(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    def fake_run(cmd, **kwargs):
        raise OSError("no such file")

    monkeypatch.setattr(llm_client.subprocess, "run", fake_run)

    result = llm_client.run_text_prompt("hello")
    assert result == ""


def test_run_text_prompt_nonzero_returncode_returns_empty_string(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(llm_client.subprocess, "run", _fake_run_fail)

    result = llm_client.run_text_prompt("hello")
    assert result == ""


def test_run_text_prompt_empty_stdout_returns_empty_string(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(llm_client.subprocess, "run", fake_run)

    result = llm_client.run_text_prompt("hello")
    assert result == ""


def test_run_text_prompt_unsupported_backend_raises_value_error():
    with pytest.raises(ValueError, match="Unsupported"):
        llm_client.run_text_prompt("hello", backend="unknown_backend")


def test_run_text_prompt_gemini_cli_backend_raises_value_error():
    with pytest.raises(ValueError, match="Unsupported"):
        llm_client.run_text_prompt("hello", backend="gemini-cli")


def test_run_text_prompt_explicit_agy_backend(monkeypatch):
    seen_cmd = []

    def fake_run(cmd, **kwargs):
        seen_cmd.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="result", stderr="")

    monkeypatch.setattr(llm_client.subprocess, "run", fake_run)

    result = llm_client.run_text_prompt("hello", backend="agy")
    assert result == "result"
    assert seen_cmd[0][0] == "agy"


def test_run_text_prompt_agy_model_env_var(monkeypatch):
    seen_cmd = []

    def fake_run(cmd, **kwargs):
        seen_cmd.extend(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(llm_client.subprocess, "run", fake_run)
    monkeypatch.setenv("AGY_MODEL", "my-custom-model")

    llm_client.run_text_prompt("hello", backend="agy")
    assert "my-custom-model" in seen_cmd


def test_run_text_prompt_passes_cwd(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(llm_client.subprocess, "run", fake_run)

    result = llm_client.run_text_prompt("hello", backend="agy", cwd="/tmp")

    assert result == "ok"
    assert seen["cwd"] == "/tmp"


def test_clean_agy_output_strips_agent_noise():
    raw = (
        "No more tools to call. I'm waiting for the local memory search to complete.\n"
        "No more tools to call. I am waiting for the model downloading / search task to complete.\n"
        "真正的摘要內容\n第二行\n"
    )
    assert llm_client._clean_agy_output(raw) == "真正的摘要內容\n第二行"


def test_run_text_prompt_agy_cleans_noise(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=(
                "No more tools to call. I'm waiting for the local memory search to complete.\n"
                "摘要內容\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(llm_client.subprocess, "run", fake_run)

    result = llm_client.run_text_prompt("hello", backend="agy")
    assert result == "摘要內容"


def test_clean_agy_output_rejects_timeout_marker():
    assert llm_client._clean_agy_output("Error: timed out waiting for response") == ""


def test_run_text_prompt_agy_retries_once_after_timeout_marker(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            return subprocess.CompletedProcess(cmd, 0, stdout="Error: timed out waiting for response", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="有效摘要", stderr="")

    monkeypatch.setattr(llm_client.subprocess, "run", fake_run)

    result = llm_client.run_text_prompt("hello", backend="agy")
    assert result == "有效摘要"
    assert len(calls) == 2


def test_run_text_prompt_uses_google_api_backend_when_requested(monkeypatch):
    calls = {}

    def fake_google(prompt, *, timeout, model):
        calls["prompt"] = prompt
        calls["timeout"] = timeout
        calls["model"] = model
        return "ok"

    monkeypatch.setattr(llm_client, "_run_google_api_prompt", fake_google)

    result = llm_client.run_text_prompt(
        "hello",
        timeout=30,
        backend="google_api",
        gemini_model="gemini-2.5-flash-lite",
    )

    assert result == "ok"
    assert calls == {
        "prompt": "hello",
        "timeout": 30,
        "model": "gemini-2.5-flash-lite",
    }


def test_run_text_prompt_auto_prefers_google_api_when_key_present(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(llm_client, "_run_google_api_prompt", lambda *args, **kwargs: "api")

    result = llm_client.run_text_prompt("hello", backend="auto")

    assert result == "api"


def test_run_text_prompt_google_api_returns_empty_on_retryable_failure(monkeypatch):
    class RetryableError(RuntimeError):
        pass

    attempts = {"count": 0}

    def fake_google(prompt, *, timeout, model):
        del prompt, timeout, model
        attempts["count"] += 1
        raise RetryableError("deadline exceeded")

    monkeypatch.setattr(llm_client, "_run_google_api_prompt", fake_google)
    monkeypatch.setattr(llm_client, "_is_retryable_google_error", lambda exc: True)

    result = llm_client.run_text_prompt("hello", backend="google_api")

    assert result == ""
    assert attempts["count"] == 2


def test_run_google_api_prompt_extracts_text_from_candidate(monkeypatch):
    class FakeResponse:
        text = "  answer  "

    class FakeModels:
        def generate_content(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(llm_client, "_build_google_client", lambda: FakeClient())

    result = llm_client._run_google_api_prompt("hi", timeout=15, model="gemini-2.5-flash-lite")

    assert result == "answer"


def test_run_google_api_prompt_returns_empty_when_response_text_missing(monkeypatch):
    class FakeResponse:
        text = None

    class FakeModels:
        def generate_content(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(llm_client, "_build_google_client", lambda: FakeClient())

    result = llm_client._run_google_api_prompt("hi", timeout=15, model="gemini-2.5-flash-lite")

    assert result == ""
