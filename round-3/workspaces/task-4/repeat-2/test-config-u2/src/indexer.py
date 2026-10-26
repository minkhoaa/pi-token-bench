"""Incremental file indexer implementation."""

import os
import tempfile
from pathlib import Path

from src.exceptions import IndexerError, ManifestWriteError
from src.hasher import compute_sha256
from src.manifest import format_manifest, load_manifest


def _write_manifest_atomically(manifest_path: Path, files: dict[str, str]) -> None:
    """Write a complete manifest beside its destination, then atomically replace it."""
    temporary_path = None
    file_descriptor = None
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{manifest_path.name}.",
            suffix=".tmp",
            dir=manifest_path.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as temporary_file:
            file_descriptor = None
            temporary_file.write(format_manifest(files))
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, manifest_path)
        temporary_path = None
    except OSError as error:
        raise ManifestWriteError(f"Failed to write manifest: {error}") from error
    finally:
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


def sync_index(root: Path, manifest_path: Path) -> dict:
    """Synchronize manifest with regular .txt files below root."""
    root = Path(os.path.abspath(root))
    manifest_path = Path(os.path.abspath(manifest_path))

    if root.is_symlink() or not root.is_dir():
        raise IndexerError(f"Root path is not a directory: {root}")

    # Let load_manifest's CorruptManifestError propagate without touching the manifest.
    old_manifest = load_manifest(manifest_path)
    current_files: dict[str, str] = {}
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
                        if not entry.name.endswith(".txt"):
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue

                        file_path = Path(entry.path)
                        relative_path = file_path.relative_to(root).as_posix()
                        current_files[relative_path] = compute_sha256(file_path)
                    except OSError:
                        # Files may disappear or become inaccessible during scanning.
                        continue
        except OSError:
            # A directory may disappear after it is added to the traversal stack.
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
