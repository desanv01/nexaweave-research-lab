"""Pure V2 original-byte validation; Main executes after source freeze."""
import hashlib
from uuid import uuid4

import pytest

from nexaweave_storage import InvalidProject, SourceStore, StorageError
from nexaweave_storage.source import (SourceRecord, _validated_binary,
    MAX_ORIGINAL_BYTES)


def test_original_input_bound_before_connection():
    calls = []
    store = SourceStore(lambda: calls.append(1))
    for value in (None, "PDF", b"", b"x" * (MAX_ORIGINAL_BYTES + 1)):
        with pytest.raises(InvalidProject):
            store.ingest_pdf("owner", uuid4(), uuid4(), "pdf", "text", [], value)
    assert calls == []


def test_binary_row_requires_exact_bytes_digest_and_type():
    source = SourceRecord(uuid4(), uuid4(), "pdf", "text", hashlib.sha256(b"text").hexdigest(),
        4, 4, None, ())
    data = b"%PDF-1.7\ntext\n%%EOF"
    digest = hashlib.sha256(data).hexdigest()
    accepted = _validated_binary(source, (1, "application/pdf", len(data), digest, data))
    assert accepted.content == data and accepted.sha256 == digest
    for row in ((2, "application/pdf", len(data), digest, data),
                (1, "application/octet-stream", len(data), digest, data),
                (1, "application/pdf", len(data) + 1, digest, data),
                (1, "application/pdf", len(data), "0" * 64, data),
                (1, "application/pdf", len(data), digest, data + b"x")):
        with pytest.raises(StorageError):
            _validated_binary(source, row)
