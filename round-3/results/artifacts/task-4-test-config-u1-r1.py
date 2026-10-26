"""Incremental file indexer implementation."""

import os
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
    manifest_path = Path(manifest_path).resolve()

    if not root.is_dir():
        raise IndexerError(f"Root path is not a directory: {root}")

    # Let corruption abort before scanning or writing anything.
    old_manifest = load_manifest(manifest_path)

    current_files: Dict[str, str] = {}
    directories = [root]
    while directories:
        directory = directories.pop()
        if directory.is_symlink():
            continue
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            directories.append(Path(entry.path))
                        elif entry.name.endswith(".txt") and entry.is_file(follow_symlinks=False):
                            file_path = Path(entry.path)
                            if file_path.is_symlink():
                                continue
                            rel_path = file_path.relative_to(root).as_posix()
                            current_files[rel_path] = compute_sha256(file_path)
                    except OSError:
                        # Entries may disappear or become inaccessible during scanning.
                        continue
        except OSError:
            # A directory may disappear or become inaccessible during scanning.
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

    temp_path = None
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=manifest_path.parent,
            prefix=f".{manifest_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as f:
            temp_path = Path(f.name)
            f.write(format_manifest(current_files))
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, manifest_path)
        temp_path = None
    except OSError as exc:
        raise ManifestWriteError(f"Failed to write manifest {manifest_path}: {exc}") from exc
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    return {
        "added": sorted(added),
        "modified": sorted(modified),
        "deleted": sorted(deleted),
        "unchanged": sorted(unchanged),
    }
