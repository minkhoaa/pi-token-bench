"""Incremental indexer for regular .txt files."""

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
        raise IndexerError(f"Root path is not a directory: {root}")
    root = root.absolute()
    manifest_path = Path(manifest_path).absolute()

    if not root.is_dir():
        raise IndexerError(f"Root path is not a directory: {root}")

    # Load before scanning or writing so corruption is propagated unchanged.
    old_manifest = load_manifest(manifest_path)
    current_files: Dict[str, str] = {}

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        directory = Path(dirpath)
        # os.walk lists directory symlinks even with followlinks=False; prune them.
        dirnames[:] = [name for name in dirnames if not (directory / name).is_symlink()]

        for filename in filenames:
            if not filename.endswith(".txt"):
                continue

            file_path = directory / filename
            try:
                if not stat.S_ISREG(file_path.lstat().st_mode):
                    continue
                rel_path = file_path.relative_to(root).as_posix()
                current_files[rel_path] = compute_sha256(file_path)
            except OSError:
                # A file may disappear or become unreadable during the scan.
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

    manifest_data = format_manifest(current_files)
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
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(manifest_data)
            temporary.flush()
            os.fsync(temporary.fileno())

        os.replace(temporary_path, manifest_path)
        temporary_path = None
    except (OSError, UnicodeError) as error:
        raise ManifestWriteError(f"Failed to write manifest: {error}") from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass

    return {
        "added": sorted(added),
        "modified": sorted(modified),
        "deleted": sorted(deleted),
        "unchanged": sorted(unchanged),
    }
