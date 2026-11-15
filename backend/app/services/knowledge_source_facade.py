"""Bounded protected retention; DOCX extraction follows persisted authorization."""
import base64
import hashlib
import threading
import time
from dataclasses import asdict
from uuid import uuid4

from .knowledge_source_client import (KnowledgeSourceProcessClient, MAX_BYTES, HASH,
    TEXT_BYTES, encoded, text_value, declarations, metadata, validate_payload, validate_result,
    pdf_upload, pdf_receipt, original_result)
from .knowledge_transport import KnowledgeBusy, KnowledgeTransportFailure, _json_object, _uuid
from .knowledge_reader import KnowledgeReadError


def validate_upload(value):
    try:
        if type(value) is dict and value.get("format") == "pdf":
            return pdf_upload(value)
        if (type(value) is not dict or set(value) != {"schema_version", "source_revision",
                "source_name", "format", "content", "input_sha256"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError
        _uuid(value["source_revision"])
        text_value(value["source_name"], 1024, name=True)
        if type(value["input_sha256"]) is not str or not HASH.fullmatch(value["input_sha256"]):
            raise ValueError
        content = value["content"]
        if value["format"] == "text":
            text_value(content)
            binary = content.encode()
            declarations(value["source_revision"], content)
        elif value["format"] == "docx":
            if (type(content) is not str or not 0 < len(content) <= 4 * ((2 * TEXT_BYTES + 2) // 3)
                    or not content.isascii()):
                raise ValueError
            binary = base64.b64decode(content, validate=True)
            if (not 0 < len(binary) <= 2 * TEXT_BYTES
                    or base64.b64encode(binary).decode("ascii") != content):
                raise ValueError
        else:
            raise ValueError
        if hashlib.sha256(binary).hexdigest() != value["input_sha256"] or len(encoded(value)) > MAX_BYTES:
            raise ValueError
        return binary
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise KnowledgeReadError("invalid_request") from None


def validate_original_upload(value):
    try:
        return pdf_upload(value, version=2)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise KnowledgeReadError("invalid_request") from None


def validate_original_public_result(method, data, scope, payload):
    try:
        return original_result(method, data, scope, payload)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise KnowledgeTransportFailure(outcome_unknown=True) from None


def validate_public_result(method, data, scope, payload):
    """Recheck the HTTP DTO even when Main explicitly injects a facade."""
    if method != "retain":
        return validate_result(method, data, scope, payload)
    try:
        if payload["format"] == "pdf":
            return pdf_receipt(data, scope, payload)
        if type(data) is not dict or set(data) != {"schema_version", "binary_retained",
                "graph_ingestion_executed", "source", "offset_unit", "passages", "extraction"}:
            raise ValueError
        extraction = data["extraction"]
        common = {"format", "input_hash_verified", "input_sha256", "input_digest_persisted",
            "blocks_persisted", "original_document_verified", "binary_persistently_bound",
            "ocr_performed", "page_layout", "semantic_quality"}
        fields = common | ({"coverage", "excluded_parts", "blocks", "deleted_revision_text_included",
                             "field_instruction_text_included"} if payload["format"] == "docx" else set())
        if (type(extraction) is not dict or set(extraction) != fields
                or extraction["format"] != payload["format"]
                or extraction["input_sha256"] != payload["input_sha256"]
                or extraction["input_hash_verified"] is not True
                or any(extraction[k] is not False for k in ("input_digest_persisted", "blocks_persisted",
                    "original_document_verified", "binary_persistently_bound", "ocr_performed"))
                or extraction["page_layout"] != "unknown" or extraction["semantic_quality"] != "unknown"):
            raise ValueError
        core = {key: value for key, value in data.items() if key != "extraction"}
        if payload["format"] == "text":
            validate_result("retain", core, scope, {"source_revision": payload["source_revision"],
                "source_name": payload["source_name"], "text": payload["content"], "blocks": None})
        else:
            if (type(data["schema_version"]) is not int or data["schema_version"] != 1
                    or data["binary_retained"] is not False or data["graph_ingestion_executed"] is not False
                    or data["offset_unit"] != "unicode_codepoint"
                    or extraction["coverage"] != ["main_body_paragraphs", "main_body_table_cells"]
                    or extraction["excluded_parts"] != ["headers", "footers", "footnotes", "endnotes"]
                    or extraction["deleted_revision_text_included"] is not False
                    or extraction["field_instruction_text_included"] is not False):
                raise ValueError
            metadata(data["source"], scope)
            source = data["source"]
            if source["source_revision"] != payload["source_revision"] or source["source_name"] != payload["source_name"]:
                raise ValueError
            # Structural block/ID checks require only bounded codepoint space. The
            # actual facade has already joined every hash against extracted text.
            expected = declarations(payload["source_revision"], " " * source["codepoint_length"], extraction["blocks"])
            passages = data["passages"]
            if type(passages) is not list or len(passages) != len(expected):
                raise ValueError
            for item, declared in zip(passages, expected):
                if (type(item) is not dict or set(item) != {"evidence_id", "start", "end", "page", "excerpt_sha256"}
                        or type(item["start"]) is not int or type(item["end"]) is not int
                        or type(item["evidence_id"]) is not str
                        or any(item[k] != declared[k] for k in declared) or item["page"] is not None
                        or type(item["excerpt_sha256"]) is not str or not HASH.fullmatch(item["excerpt_sha256"])):
                    raise ValueError
        if len(encoded(data)) > MAX_BYTES:
            raise ValueError
        return data
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise KnowledgeTransportFailure(outcome_unknown=True) from None


class KnowledgeSourceFacade:
    def __init__(self, settings, *, client_factory=None):
        self._settings = settings
        self._factory = client_factory
        self._admission = threading.BoundedSemaphore(2)

    def _call(self, method, payload, deadline):
        validate_payload(method, payload)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise KnowledgeReadError("timeout")
        client = self._factory() if self._factory else KnowledgeSourceProcessClient(self._settings)
        if isinstance(client, KnowledgeSourceProcessClient):
            client._timeout = min(60, remaining)
        request_id = str(uuid4())
        raw = encoded({"version": 1, "request_id": request_id, "method": method,
                       "scope": self._settings.scope, "payload": payload})
        reply = client.call(raw)
        KnowledgeSourceProcessClient._validate_reply(client, reply, request_id)
        result = _json_object(reply)
        if not result["ok"]:
            raise KnowledgeReadError(result["error"]["code"])
        data = validate_result(method, result["result"], self._settings.scope, payload)
        if time.monotonic() > deadline:
            raise KnowledgeTransportFailure(outcome_unknown=method in {"retain", "retain_pdf", "retain_pdf_binary"})
        return data

    def execute(self, method, graph_id, payload):
        if graph_id != self._settings.display_graph_id:
            raise KnowledgeReadError("not_found")
        binary = validate_upload(payload) if method == "retain" else None
        if method == "retain_pdf_binary":
            validate_original_upload(payload)
        if method not in {"retain", "retain_pdf_binary"}:
            validate_payload(method, payload)
        if not self._admission.acquire(blocking=False):
            raise KnowledgeBusy()
        deadline = time.monotonic() + 60
        try:
            if method not in {"retain", "retain_pdf_binary"}:
                return self._call(method, payload, deadline)
            self._call("context", {}, deadline)
            if method == "retain_pdf_binary":
                return self._call("retain_pdf_binary", payload, deadline)
            if payload["format"] == "pdf":
                # Native extraction and retained-source mutation occur together
                # in the fixed installed source child after persisted authority.
                return self._call("retain_pdf", payload, deadline)
            blocks = None
            extraction = {"format": "text", "input_hash_verified": True,
                "input_sha256": payload["input_sha256"], "binary_persistently_bound": False,
                "input_digest_persisted": False, "blocks_persisted": False,
                "original_document_verified": False, "ocr_performed": False,
                "page_layout": "unknown", "semantic_quality": "unknown"}
            if payload["format"] == "docx":
                from ..utils.docx_extraction import extract_docx, DocxError
                try:
                    extracted = extract_docx(binary, max_file_bytes=2 * TEXT_BYTES,
                                             max_text_chars=TEXT_BYTES)
                except DocxError as error:
                    raise KnowledgeReadError(error.code if error.code == "limit_exceeded" else "invalid_request") from None
                text = extracted.text
                blocks = []
                for ordinal, block in enumerate(extracted.blocks):
                    item = asdict(block)
                    item.update(ordinal=ordinal, empty=block.start == block.end)
                    blocks.append(item)
                extraction.update(format="docx", coverage=list(extracted.coverage),
                    excluded_parts=list(extracted.excluded_parts), blocks=blocks,
                    deleted_revision_text_included=False, field_instruction_text_included=False)
            else:
                text = payload["content"]
            retained = {"source_revision": payload["source_revision"], "source_name": payload["source_name"],
                        "text": text, "blocks": blocks}
            validate_payload("retain", retained)
            # Reserve response room before the mutation, including transient blocks.
            if len(encoded(extraction)) + 100 * 512 + 4096 > MAX_BYTES:
                raise KnowledgeReadError("result_too_large")
            result = self._call("retain", retained, deadline)
            result["extraction"] = extraction
            return result
        finally:
            self._admission.release()
