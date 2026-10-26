"""Streaming SHA-256 computation utility."""

import hashlib
from pathlib import Path

DEFAULT_CHUNK_SIZE = 64 * 1024  # 64 KB chunks


def compute_sha256(file_path: Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    """Compute SHA-256 hex digest incrementally without loading whole file into memory."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()
