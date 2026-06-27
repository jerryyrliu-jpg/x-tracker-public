import logging
import os
import shutil
import subprocess


logger = logging.getLogger(__name__)


def _agy_command(prompt: str, model: str | None) -> list[str]:
    cmd = ["agy", "--print"]
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt)
    return cmd


def _gemini_command(prompt: str, model: str | None) -> list[str]:
    cmd = ["gemini"]
    if model:
        cmd.extend(["--model", model])
    cmd.extend(["--prompt", prompt])
    return cmd


def _resolve_backend(backend: str) -> str:
    if backend != "auto":
        return backend
    if shutil.which("agy"):
        return "agy"
    return "gemini-cli"


def run_text_prompt(
    prompt: str,
    *,
    timeout: int = 300,
    backend: str = "auto",
    agy_model: str | None = None,
    gemini_model: str | None = None,
) -> str:
    """Run a text prompt through the configured CLI backend."""
    resolved_backend = _resolve_backend(backend)
    if resolved_backend == "agy":
        cmd = _agy_command(prompt, agy_model or os.getenv("AGY_MODEL") or os.getenv("LLM_MODEL"))
    elif resolved_backend == "gemini-cli":
        cmd = _gemini_command(prompt, gemini_model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"))
    else:
        raise ValueError(f"Unsupported backend: {resolved_backend}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning("%s call timed out.", resolved_backend)
        return ""
    except Exception as exc:
        logger.warning("%s call error: %s", resolved_backend, type(exc).__name__)
        return ""

    if result.returncode != 0 or not result.stdout.strip():
        logger.warning("%s call failed: %s", resolved_backend, result.stderr[:300])
        return ""
    return result.stdout
