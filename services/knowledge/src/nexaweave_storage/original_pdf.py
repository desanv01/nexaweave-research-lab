"""Format and size checks shared by retained and portable original PDFs."""
import hashlib

MAX_ORIGINAL_BYTES = 2 * 1024 * 1024


def original_pdf_sha256(content: bytes) -> str:
    if (type(content) is not bytes or not 0 < len(content) <= MAX_ORIGINAL_BYTES
            or b"%PDF-" not in content[:1024] or not content.rstrip().endswith(b"%%EOF")):
        raise ValueError("invalid original PDF")
    return hashlib.sha256(content).hexdigest()
