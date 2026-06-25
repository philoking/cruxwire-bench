"""The versioned corpus-archive contract.

This package is the single source of truth for the corpus-archive record format
(see console_spec.md → "Repos and Build Layout" → "The seam is the schema").
The cruxwire archive-writer module conforms to *this* definition; it never
imports it (the repos don't share code), it just emits records that match.

Keep changes additive: new columns are nullable, existing columns are never
repurposed, and SCHEMA_VERSION bumps on every additive change.
"""

from .corpus_schema import (
    SCHEMA_VERSION,
    CORPUS_FIELDS,
    DUCKDB_COLUMN_TYPES,
    PROD_EMBEDDING_DIM,
    PROD_EMBEDDING_MODEL,
    validate_record,
)

__all__ = [
    "SCHEMA_VERSION",
    "CORPUS_FIELDS",
    "DUCKDB_COLUMN_TYPES",
    "PROD_EMBEDDING_DIM",
    "PROD_EMBEDDING_MODEL",
    "validate_record",
]
