"""Validate retained source input before loading PDF runtime dependencies."""
import base64
import hashlib
import re
from unicodedata import category
from uuid import UUID

TEXT_BYTES = 1024 * 1024

def uuid_value(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError
    return UUID(value)


def text_value(value, *, name=False):
    if (type(value) is not str or not value.strip() or (name and len(value) > 256)
            or any((ord(c) < 32 and (name or c not in "\t\n\r")) or ord(c) == 127
                   or (name and category(c) == "Cc") for c in value)
            or len(value.encode()) > (1024 if name else TEXT_BYTES)):
        raise ValueError


def pdf_input(payload, *, version=1):
    """Validate decoded input and digest before importing any PDF runtime."""
    if (type(payload) is not dict or set(payload) != {"schema_version", "source_revision",
            "source_name", "format", "content", "input_sha256"}
            or type(payload["schema_version"]) is not int or payload["schema_version"] != version
            or type(payload["format"]) is not str or payload["format"] != "pdf"):
        raise ValueError
    uuid_value(payload["source_revision"])
    text_value(payload["source_name"], name=True)
    content = payload["content"]
    if (type(content) is not str or not content.isascii()
            or not 0 < len(content) <= 4 * ((2 * TEXT_BYTES + 2) // 3)
            or type(payload["input_sha256"]) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", payload["input_sha256"])):
        raise ValueError
    binary = base64.b64decode(content, validate=True)
    if (not 0 < len(binary) <= 2 * TEXT_BYTES
            or base64.b64encode(binary).decode("ascii") != content
            or hashlib.sha256(binary).hexdigest() != payload["input_sha256"]
            or b"%PDF-" not in binary[:1024] or not binary.rstrip().endswith(b"%%EOF")):
        raise ValueError
    return binary
