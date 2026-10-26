"""
Incremental file indexer implementation.
NOTE: This implementation initially contained defects in symlink handling, atomic
persistence, path normalization, and corruption recovery; they have been repaired.
"""

import os
import tempfile
from pathlib import Path
from typing import Dict

from src.exceptions import IndexerError, ManifestWriteError
from src.hasher import compute_sha256
from src.manifest import format_manifest, load_manifest


def _discover_files(root: Path) -> Dict[str, str]:
    """Return hashes for regular .txt files, without traversing symbolic links."""
    current_files: Dict[str, str] = {}
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
                        relative_path = file_path.relative_to(root).as_posix()
                        current_files[relative_path] = compute_sha256(file_path)
                    except OSError:
                        # Ignore entries that disappear or become inaccessible during scanning.
                        continue
        except OSError:
            # A directory may disappear or become inaccessible after it was discovered.
            continue

    return current_files


def _write_manifest_atomically(manifest_path: Path, files_map: Dict[str, str]) -> None:
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
            temporary_file.write(format_manifest(files_map))
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_path, manifest_path)
        temporary_path = None
    except OSError as exc:
        raise ManifestWriteError(f"Failed to write manifest: {manifest_path}") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def sync_index(root: Path, manifest_path: Path) -> dict:
    """
    Synchronize the manifest with current regular .txt files in root.
    Former defects: symlink traversal, non-atomic writes, swallowed corruption, and
    unsorted results are now repaired.
    """
    root = Path(root).resolve()
    manifest_path = Path(manifest_path).resolve()

    if not root.is_dir():
        raise IndexerError(f"Root path is not a directory: {root}")

    # Former defect: Corrupt manifests were swallowed; load errors now propagate unchanged.
    old_manifest = load_manifest(manifest_path)

    # Former defect: os.walk followed symlinks; explicit non-following traversal prevents loops.
    current_files = _discover_files(root)

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

    # Former defect: Manifest writes were direct; replacement is now atomic.
    _write_manifest_atomically(manifest_path, current_files)

    return {
        "added": sorted(added),
        "modified": sorted(modified),
        "deleted": sorted(deleted),
        "unchanged": sorted(unchanged),
    }
