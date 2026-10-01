"""Admission policy and safe failures for ontology uploads."""

import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class UploadPolicy:
    max_files: int = 20
    max_file_bytes: int = 50 * 1024 * 1024
    max_total_bytes: int = 50 * 1024 * 1024
    max_text_chars: int = 5_000_000
    max_aggregate_chars: int = 5_000_000
    max_pdf_pages: int = 500
    max_requirement_chars: int = 20_000
    max_project_name_chars: int = 200
    max_context_chars: int = 20_000
    max_filename_chars: int = 255
    phase_seconds: int = 90
    child_seconds: int = 30

    def __post_init__(self):
        if any(
            type(value) is not int or value <= 0
            for value in vars(self).values()
        ):
            raise ValueError("upload policy must use positive integer limits")


DEFAULT_UPLOAD_POLICY = UploadPolicy()
_ALLOWED_EXTENSIONS = frozenset({"pdf", "md", "markdown", "txt", "docx"})


class UploadAdmissionError(ValueError):
    def __init__(self, code, status, message):
        self.code = code
        self.status = status
        self.message = message
        super().__init__(message)


class UploadLimitError(ValueError):
    code = "upload_limit_exceeded"


class UploadWriteError(OSError):
    code = "upload_storage_failed"

    def __init__(self):
        super().__init__(self.code)


def _scalar(form, key, default, max_chars, required=False):
    values = form.getlist(key)
    if len(values) > 1:
        raise UploadAdmissionError("invalid_upload_fields", 400, "Invalid upload fields")
    value = values[0] if values else default
    if not isinstance(value, str):
        raise UploadAdmissionError("invalid_upload_fields", 400, "Invalid upload fields")
    if len(value) > max_chars:
        raise UploadAdmissionError("upload_field_limit", 413, "Upload field limit exceeded")
    if required and not value.strip():
        raise UploadAdmissionError("invalid_upload_fields", 400, "Invalid upload fields")
    return value


def _valid_filename(filename, policy):
    if not isinstance(filename, str) or not filename:
        raise UploadAdmissionError("invalid_upload_file", 400, "Invalid upload file")
    if len(filename) > policy.max_filename_chars:
        raise UploadAdmissionError("upload_filename_limit", 413, "Upload filename limit exceeded")
    if (
        "/" in filename
        or "\\" in filename
        or any(unicodedata.category(char) == "Cc" for char in filename)
    ):
        raise UploadAdmissionError("invalid_upload_file", 400, "Invalid upload file")
    stem, dot, extension = filename.rpartition(".")
    if not dot or not stem or extension.lower() not in _ALLOWED_EXTENSIONS:
        raise UploadAdmissionError("invalid_upload_file", 400, "Invalid upload file")


def admit_ontology_upload(form, files, policy=DEFAULT_UPLOAD_POLICY):
    """Validate all visible fields and files without reading upload streams."""
    # A malformed multipart file part can be parsed as an ordinary form field.
    # Treat that ambiguity as a rejected batch, even when other files are valid.
    if form.getlist("files"):
        raise UploadAdmissionError("invalid_upload_file", 400, "Invalid upload file")
    requirement = _scalar(
        form, "simulation_requirement", "", policy.max_requirement_chars, required=True
    )
    project_name = _scalar(
        form, "project_name", "Unnamed Project", policy.max_project_name_chars, required=True
    )
    context = _scalar(form, "additional_context", "", policy.max_context_chars)
    if any(key != "files" for key in files.keys()):
        raise UploadAdmissionError("invalid_upload_file", 400, "Invalid upload file")
    uploads = files.getlist("files")
    if not uploads:
        raise UploadAdmissionError("invalid_upload_file", 400, "Invalid upload file")
    if len(uploads) > policy.max_files:
        raise UploadAdmissionError("upload_file_count_limit", 413, "Upload file count exceeded")
    for upload in uploads:
        _valid_filename(upload.filename, policy)
    return requirement, project_name, context, uploads
