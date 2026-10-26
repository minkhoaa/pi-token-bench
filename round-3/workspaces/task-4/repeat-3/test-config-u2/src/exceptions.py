"""Exceptions for the incremental file indexer."""

class IndexerError(Exception):
    """Base exception for all indexer errors."""
    pass


class CorruptManifestError(IndexerError):
    """Raised when an existing manifest cannot be parsed or has an invalid schema."""
    pass


class ManifestWriteError(IndexerError):
    """Raised when manifest writing or atomic replacement fails."""
    pass
