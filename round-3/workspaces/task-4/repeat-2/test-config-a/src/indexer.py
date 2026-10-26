"""Crash-safe incremental indexer for regular ``.txt`` files."""

import os
import tempfile
from pathlib import Path
from typing import Dict

from src.exceptions import IndexerError, ManifestWriteError
from src.hasher import compute_sha256
from src.manifest import format_manifest, load_manifest


def _write_manifest_atomically(manifest_path: Path, files: Dict[str, str]) -> None:
    """Write a complete manifest beside its destination, then atomically replace it."""
    temp_path = None
    open_fd = None
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        open_fd, temp_name = tempfile.mkstemp(
            prefix=f".{manifest_path.name}.",
            suffix=".tmp",
            dir=manifest_path.parent,
        )
        temp_path = Path(temp_name)
        with os.fdopen(open_fd, "w", encoding="utf-8") as output:
            open_fd = None  # fdopen now owns the descriptor
            output.write(format_manifest(files))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, manifest_path)
        temp_path = None
    except OSError as exc:
        raise ManifestWriteError(f"Failed to write manifest {manifest_path}: {exc}") from exc
    finally:
        if open_fd is not None:
            try:
                os.close(open_fd)
            except OSError:
                pass
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass


def sync_index(root: Path, manifest_path: Path) -> dict:
    """Synchronize the manifest with regular ``.txt`` files found beneath ``root``."""
    root = Path(root)
    if root.is_symlink():
        raise IndexerError(f"Root path is not a directory: {root}")
    root = Path(os.path.abspath(os.fspath(root)))
    manifest_path = Path(manifest_path).resolve()

    if not root.is_dir():
        raise IndexerError(f"Root path is not a directory: {root}")

    # In particular, do not catch CorruptManifestError: a bad manifest must be
    # preserved and reported rather than silently replaced with a fresh index.
    old_manifest = load_manifest(manifest_path)
    current_files: Dict[str, str] = {}

    # An explicit scandir traversal makes every symlink test non-following and
    # avoids both os.walk's symlink-dir pitfalls and recursive Python calls.
    directories = [root]
    while directories:
        directory = directories.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            directories.append(Path(entry.path))
                            continue
                        if not entry.name.endswith(".txt") or not entry.is_file(
                            follow_symlinks=False
                        ):
                            continue

                        file_path = Path(entry.path)
                        rel_path = file_path.relative_to(root).as_posix()
                        try:
                            current_files[rel_path] = compute_sha256(file_path)
                        except OSError:
                            # A file can disappear or become unreadable after scandir.
                            continue
                    except OSError:
                        # Ignore entries that vanish or become inaccessible during the scan.
                        continue
        except OSError:
            # A directory may disappear or become inaccessible during traversal.
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

    _write_manifest_atomically(manifest_path, current_files)

    return {
        "added": sorted(added),
        "modified": sorted(modified),
        "deleted": sorted(deleted),
        "unchanged": sorted(unchanged),
    }
