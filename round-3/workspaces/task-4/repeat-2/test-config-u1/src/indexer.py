"""Incremental file indexer implementation."""

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
    root = Path(root)
    if root.is_symlink():
        raise IndexerError(f"Root path is a symbolic link: {root}")
    root = root.resolve()
    manifest_path = Path(manifest_path).resolve()

    if not root.is_dir():
        raise IndexerError(f"Root path is not a directory: {root}")

    # Let corruption abort before any write can touch the existing manifest.
    old_manifest = load_manifest(manifest_path)
    current_files: Dict[str, str] = {}

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        directory = Path(dirpath)
        dirnames[:] = [name for name in dirnames if not (directory / name).is_symlink()]
        for fname in filenames:
            if not fname.endswith(".txt"):
                continue
            file_path = directory / fname
            try:
                if not stat.S_ISREG(file_path.lstat().st_mode):
                    continue
                current_files[file_path.relative_to(root).as_posix()] = compute_sha256(file_path)
            except OSError:
                # A file may disappear or become unreadable during the scan.
                continue

    added = []
    modified = []
    unchanged = []
    for path in sorted(current_files):
        new_hash = current_files[path]
        if path not in old_manifest:
            added.append(path)
        elif old_manifest[path] != new_hash:
            modified.append(path)
        else:
            unchanged.append(path)
    deleted = sorted(path for path in old_manifest if path not in current_files)

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
            temporary_file.write(format_manifest(current_files))
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, manifest_path)
    except OSError as exc:
        raise ManifestWriteError(f"Failed to write manifest {manifest_path}: {exc}") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return {
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "unchanged": unchanged,
    }
