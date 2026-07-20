import logging
import os
import subprocess


logger = logging.getLogger(__name__)


def _clean_agy_output(text: str) -> str:
    """Strip known agent-status chatter from agy print output."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        lower = stripped.lower()
        if lower == "error: timed out waiting for response":
            continue
        if lower.startswith("no more tools to call."):
            continue
        if "waiting for the model downloading / search task to complete" in lower:
            continue
        if "waiting for the local memory search to complete" in lower:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _agy_command(prompt: str, model: str | None) -> list[str]:
    cmd = ["agy", "--print"]
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt)
    return cmd


def _resolve_backend(backend: str) -> str:
    if backend != "auto":
        return backend
    if os.getenv("GOOGLE_API_KEY"):
        return "google_api"
    return "agy"


def _retry_count_for_backend(backend: str) -> int:
    if backend in {"google_api", "agy"}:
        return 2
    return 1


def _build_google_client():
    from google import genai

    return genai.Client(api_key=os.environ["GOOGLE_API_KEY"])


def _run_google_api_prompt(prompt: str, *, timeout: int, model: str) -> str:
    client = _build_google_client()
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config={
            "http_options": {"timeout": timeout * 1000},
            "temperature": 0.2,
        },
    )
    return ((getattr(response, "text", None) or "")).strip()


def _is_retryable_google_error(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = ("deadline", "timeout", "temporar", "unavailable", "503", "429")
    return any(marker in text for marker in markers)


def _run_backend(cmd: list[str], *, timeout: int, cwd: str | None = None) -> str:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        logger.warning("%s call timed out.", cmd[0])
        return ""
    except Exception as exc:
        logger.warning("%s call error: %s", cmd[0], type(exc).__name__)
        return ""

    if result.returncode != 0 or not result.stdout.strip():
        logger.warning("%s call failed: %s", cmd[0], result.stderr[:300])
        return ""
    output = result.stdout
    if cmd and cmd[0] == "agy":
        logger.warning("agy raw stdout (first 300): %r", output[:300])
        output = _clean_agy_output(output)
        if not output:
            logger.warning("agy output only contained agent noise.")
            return ""
    return output


def run_text_prompt(
    prompt: str,
    *,
    timeout: int = 300,
    backend: str = "auto",
    agy_model: str | None = None,
    gemini_model: str | None = None,
    cwd: str | None = None,
) -> str:
    """Run a text prompt through the configured CLI backend."""
    resolved_backend = _resolve_backend(backend)
    if resolved_backend == "google_api":
        model = gemini_model or os.getenv("GEMINI_MODEL") or "gemini-2.5-flash-lite"
        for attempt in range(_retry_count_for_backend("google_api")):
            try:
                output = _run_google_api_prompt(prompt, timeout=timeout, model=model)
            except Exception as exc:
                logger.warning("google_api call error: %s", type(exc).__name__)
                if attempt == 0 and _is_retryable_google_error(exc):
                    continue
                return ""
            if output.strip():
                return output.strip()
            if attempt == 0:
                logger.warning("google_api backend returned empty output; retrying once.")
        return ""
    if resolved_backend == "agy":
        cmd = _agy_command(prompt, agy_model or os.getenv("AGY_MODEL") or os.getenv("LLM_MODEL"))
        for attempt in range(_retry_count_for_backend("agy")):
            output = _run_backend(cmd, timeout=timeout, cwd=cwd)
            if output:
                return output
            if attempt == 0:
                logger.warning("agy backend returned no usable output; retrying once.")
        logger.warning("agy backend failed after 2 attempts.")
        return ""
    raise ValueError(f"Unsupported backend: {resolved_backend}")
