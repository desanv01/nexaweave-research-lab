"""Explicit, ownership-scoped project metadata revisions.

Importing this package never connects to PostgreSQL or runs migrations.
"""

from .store import (Conflict, MigrationMismatch, NotFound, ProjectRecord,
                    ProjectStore, StorageError, migrate)
from .validation import InvalidProject, canonical_payload, validate_evidence, validate_snapshot

__all__ = ["Conflict", "InvalidProject", "MigrationMismatch", "NotFound",
           "ProjectRecord", "ProjectStore", "StorageError", "canonical_payload",
           "migrate", "validate_evidence", "validate_snapshot"]
