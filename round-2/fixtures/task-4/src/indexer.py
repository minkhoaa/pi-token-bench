"""
Incremental file indexer implementation.
NOTE: This implementation contains known defects in symlink handling, atomic persistence,
path normalization, and corruption recovery that must be diagnosed and repaired.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List

from src.exceptions import CorruptManifestError, IndexerError
from src.hasher import compute_sha256
from src.manifest import format_manifest, load_manifest


def sync_index(root: Path, manifest_path: Path) -> dict:
    """
    Synchronize the manifest with current regular .txt files in root.
    DEFECTIVE: Follows symlinks, non-atomic writes, swallowed corruption, unsorted results.
    """
    root = Path(root).resolve()
    manifest_path = Path(manifest_path).resolve()

    if not root.is_dir():
        raise IndexerError(f"Root path is not a directory: {root}")

    # Defect: Swallows corrupt manifest instead of raising CorruptManifestError
    try:
        old_manifest = load_manifest(manifest_path)
    except CorruptManifestError:
        # FLAW: Discards corrupt manifest instead of aborting safely
        old_manifest = {}

    current_files: Dict[str, str] = {}

    # Defect: Uses os.walk without followlinks=False check, follows symlinks and symlink loops
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        for fname in filenames:
            # Defect: Doesn't properly restrict to regular files or ignore symlinks
            file_path = Path(dirpath) / fname
            if fname.endswith(".txt"):
                # Defect: Uses non-posix relative path
                rel_path = str(file_path.relative_to(root))

                # Defect: Reads whole file into memory instead of streaming
                try:
                    with open(file_path, "rb") as f:
                        data = f.read()
                    h = hashlib.sha256(data).hexdigest()
                    current_files[rel_path] = h
                except OSError:
                    # Ignore unreadable file
                    continue

    added = []
    modified = []
    unchanged = []
    deleted = []

    for path, new_hash in current_files.items():
        if path not in old_manifest:
            added.append(path)
        elif old_manifest[path] != new_hash:
            modified.append(path)
        else:
            unchanged.append(path)

    for path in old_manifest:
        if path not in current_files:
            deleted.append(path)

    # Defect: Non-atomic file write directly to manifest_path
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write(format_manifest(current_files))

    # Defect: Returns unsorted lists
    return {
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "unchanged": unchanged,
    }
