"""Incremental file indexer implementation."""

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

        with os.fdopen(open_fd, "w", encoding="utf-8", newline="\n") as stream:
            open_fd = None  # fdopen owns the descriptor from this point on
            stream.write(format_manifest(files))
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(temp_path, manifest_path)
        temp_path = None

        # Persist the directory entry too where directory fsync is supported.
        try:
            directory_fd = os.open(manifest_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # Some platforms/filesystems do not allow opening or syncing directories.
            pass
    except Exception as exc:
        raise ManifestWriteError(f"Failed to write manifest atomically: {exc}") from exc
    finally:
        if open_fd is not None:
            try:
                os.close(open_fd)
            except OSError:
                pass
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


def sync_index(root: Path, manifest_path: Path) -> dict:
    """Synchronize the manifest with regular ``.txt`` files found under *root*."""
    requested_root = Path(root).absolute()
    if requested_root.is_symlink():
        raise IndexerError(f"Root path must not be a symbolic link: {requested_root}")
    root = requested_root.resolve()
    manifest_path = Path(manifest_path).absolute()

    if not root.is_dir():
        raise IndexerError(f"Root path is not a directory: {root}")

    # Let corruption errors propagate: the existing manifest must remain untouched.
    old_manifest = load_manifest(manifest_path)
    current_files: Dict[str, str] = {}

    # Do not follow directory symlinks. Prune them explicitly as well, and check
    # file entries with lstat-like symlink checks before hashing.
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        directory = Path(dirpath)
        dirnames[:] = sorted(
            dirname for dirname in dirnames
            if not (directory / dirname).is_symlink()
        )

        for filename in filenames:
            if not filename.endswith(".txt"):
                continue

            file_path = directory / filename
            try:
                if file_path.is_symlink() or not file_path.is_file():
                    continue
                relative_path = file_path.relative_to(root).as_posix()
                current_files[relative_path] = compute_sha256(file_path)
            except (OSError, ValueError):
                # Files can disappear or become inaccessible between enumeration
                # and hashing; omit them from this scan rather than failing the run.
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
