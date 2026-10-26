"""
Incremental file indexer implementation.
"""

import os
import stat
import tempfile
from pathlib import Path
from typing import Dict

from src.exceptions import IndexerError, ManifestWriteError
from src.hasher import compute_sha256
from src.manifest import format_manifest, load_manifest


def sync_index(root: Path, manifest_path: Path) -> dict:
    """Synchronize the manifest with current regular .txt files in root."""
    root = Path(os.path.abspath(os.fspath(root)))
    manifest_path = Path(manifest_path).resolve()

    try:
        root_mode = root.lstat().st_mode
    except OSError as exc:
        raise IndexerError(f"Root path is not a directory: {root}") from exc
    if not stat.S_ISDIR(root_mode):
        raise IndexerError(f"Root path is not a directory: {root}")

    # Do not catch CorruptManifestError: a corrupt manifest must remain untouched.
    old_manifest = load_manifest(manifest_path)
    current_files: Dict[str, str] = {}

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        directory = Path(dirpath)
        # os.walk does not descend into directory symlinks with followlinks=False,
        # but pruning them here makes that policy explicit and robust.
        dirnames[:] = [name for name in dirnames if not (directory / name).is_symlink()]

        for filename in filenames:
            if not filename.endswith(".txt"):
                continue

            file_path = directory / filename
            try:
                if not stat.S_ISREG(file_path.lstat().st_mode):
                    continue
                file_hash = compute_sha256(file_path)
            except OSError:
                # Files can disappear or become unreadable between traversal and hashing.
                continue

            current_files[file_path.relative_to(root).as_posix()] = file_hash

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

    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=manifest_path.parent,
                prefix=f".{manifest_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(format_manifest(current_files))
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            os.replace(temporary_path, manifest_path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
    except OSError as exc:
        raise ManifestWriteError(f"Failed to write manifest: {manifest_path}") from exc

    return {
        "added": sorted(added),
        "modified": sorted(modified),
        "deleted": sorted(deleted),
        "unchanged": sorted(unchanged),
    }
