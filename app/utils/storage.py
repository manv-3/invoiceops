"""Safe document storage with sanitized filenames."""

from pathlib import Path
from uuid import uuid4

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
}


def safe_extension(filename: str) -> str:
    """Extract and validate the file extension from *filename*.

    Raises ``ValueError`` if the extension is not allowed.
    """
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"File type '{ext}' is not allowed. Accepted: {sorted(ALLOWED_EXTENSIONS)}"
        )
    return ext


def store_document(content: bytes, original_filename: str, incoming_dir: Path) -> str:
    """Store *content* under a UUID-based name in *incoming_dir*.

    Returns the path (as a string) to the stored file.
    """
    ext = safe_extension(original_filename)
    safe_name = f"{uuid4().hex}{ext}"
    target = incoming_dir / safe_name
    target.write_bytes(content)
    return str(target)
