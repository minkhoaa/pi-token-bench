"""Manifest data structure and validation helpers."""

import json
from pathlib import Path
from src.exceptions import CorruptManifestError

MANIFEST_VERSION = 1


def load_manifest(manifest_path: Path) -> dict[str, str]:
    """
    Load and validate existing manifest file.
    Returns mapping of normalized POSIX relative paths to SHA-256 hashes.
    Raises CorruptManifestError if manifest is invalid or corrupt.
    Returns empty dict if manifest does not exist.
    """
    if not manifest_path.exists():
        return {}

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise CorruptManifestError(f"Failed to parse manifest JSON: {e}") from e

    if not isinstance(data, dict):
        raise CorruptManifestError("Manifest root must be a JSON object")

    if data.get("version") != MANIFEST_VERSION:
        raise CorruptManifestError(f"Unsupported manifest version: {data.get('version')}")

    files = data.get("files")
    if not isinstance(files, dict):
        raise CorruptManifestError("Manifest 'files' field must be a dictionary")

    for p, h in files.items():
        if not isinstance(p, str) or not p.strip():
            raise CorruptManifestError(f"Invalid path in manifest: {p!r}")
        if not isinstance(h, str) or len(h) != 64:
            raise CorruptManifestError(f"Invalid SHA-256 hash in manifest: {h!r}")

    return files


def format_manifest(files_map: dict[str, str]) -> str:
    """Format manifest dictionary as deterministic formatted JSON string."""
    payload = {
        "version": MANIFEST_VERSION,
        "files": {k: files_map[k] for k in sorted(files_map.keys())},
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
