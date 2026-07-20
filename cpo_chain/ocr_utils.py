import shutil
import subprocess


def detect_ocr_backend() -> str | None:
    if shutil.which("tesseract"):
        return "tesseract"
    return None


def extract_text_from_image(image_path: str, backend: str | None = None) -> str:
    backend = backend or detect_ocr_backend()
    if backend == "tesseract":
        try:
            proc = subprocess.run(
                ["tesseract", image_path, "stdout"],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except subprocess.TimeoutExpired:
            return ""
        if proc.returncode != 0:
            return ""
        return proc.stdout.strip()
    return ""
