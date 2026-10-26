"""Incremental, crash-safe file indexer implementation."""

import os
import stat
import tempfile
from pathlib import Path
from typing import Dict

from src.exceptions import IndexerError, ManifestWriteError
from src.hasher import compute_sha256
from src.manifest import format_manifest, load_manifest


def sync_index(root: Path, manifest_path: Path) -> dict:
    """Synchronize the manifest with regular .txt files beneath ``root``."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise IndexerError(f"Root path is not a directory: {root}")
    root = root.resolve()
    manifest_path = Path(manifest_path).resolve()

    # Do not replace or reset a manifest that cannot be loaded safely.
    old_manifest = load_manifest(manifest_path)
    current_files: Dict[str, str] = {}

    # Prune symlinked directories explicitly; followlinks=False is a second guard.
    for dirpath, dirnames, filenames in os.walk(
        root, topdown=True, followlinks=False, onerror=lambda _error: None
    ):
        directory = Path(dirpath)
        safe_dirnames = []
        for dirname in dirnames:
            child_dir = directory / dirname
            try:
                if stat.S_ISDIR(child_dir.lstat().st_mode):
                    safe_dirnames.append(dirname)
            except OSError:
                continue
        dirnames[:] = safe_dirnames

        for filename in filenames:
            if not filename.endswith(".txt"):
                continue
            file_path = directory / filename
            try:
                # lstat rejects symlinks and non-regular files without following them.
                if not stat.S_ISREG(file_path.lstat().st_mode):
                    continue
                digest = compute_sha256(file_path)
            except OSError:
                # A file may disappear or become unreadable between listing and hashing.
                continue
            current_files[file_path.relative_to(root).as_posix()] = digest

    added = []
    modified = []
    unchanged = []
    for path, new_hash in current_files.items():
        if path not in old_manifest:
            added.append(path)
        elif old_manifest[path] != new_hash:
            modified.append(path)
        else:
            unchanged.append(path)

    deleted = [path for path in old_manifest if path not in current_files]

    _write_manifest_atomically(manifest_path, current_files)

    return {
        "added": sorted(added),
        "modified": sorted(modified),
        "deleted": sorted(deleted),
        "unchanged": sorted(unchanged),
    }


def _write_manifest_atomically(manifest_path: Path, files: Dict[str, str]) -> None:
    """Write a complete manifest beside its destination, then atomically replace it."""
    temporary_path = None
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=manifest_path.parent,
            prefix=f".{manifest_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(format_manifest(files))
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_path, manifest_path)
        temporary_path = None
    except OSError as error:
        raise ManifestWriteError(f"Failed to write manifest {manifest_path}: {error}") from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
