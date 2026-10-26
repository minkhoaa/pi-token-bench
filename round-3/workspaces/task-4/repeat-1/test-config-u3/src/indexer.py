"""Incremental file indexer implementation."""

import os
import tempfile
from pathlib import Path

from src.exceptions import IndexerError, ManifestWriteError
from src.hasher import compute_sha256
from src.manifest import format_manifest, load_manifest


def _write_manifest_atomic(manifest_path: Path, files: dict[str, str]) -> None:
    temp_path = None
    fd = None
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{manifest_path.name}.", suffix=".tmp", dir=manifest_path.parent
        )
        temp_path = Path(temp_name)
        stream = os.fdopen(fd, "w", encoding="utf-8")
        fd = None
        with stream:
            stream.write(format_manifest(files))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, manifest_path)
        temp_path = None
    except OSError as exc:
        raise ManifestWriteError(f"Failed to write manifest: {manifest_path}") from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass


def sync_index(root: Path, manifest_path: Path) -> dict:
    """Synchronize the manifest with regular `.txt` files under ``root``."""
    root = Path(root).absolute()
    manifest_path = Path(manifest_path).absolute()
    if root.is_symlink() or not root.is_dir():
        raise IndexerError(f"Root path is not a directory: {root}")

    # Load before scanning or writing; corruption must leave the manifest untouched.
    old_manifest = load_manifest(manifest_path)
    current_files: dict[str, str] = {}

    pending = [(root, "")]
    while pending:
        directory, relative_directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    relative_path = (
                        f"{relative_directory}/{entry.name}"
                        if relative_directory
                        else entry.name
                    )
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append((Path(entry.path), relative_path))
                        elif entry.name.endswith(".txt") and entry.is_file(
                            follow_symlinks=False
                        ):
                            current_files[relative_path] = compute_sha256(Path(entry.path))
                    except OSError:
                        # Files and directories may disappear during traversal or hashing.
                        continue
        except OSError:
            # An unreadable or concurrently removed directory is skipped.
            continue

    added = sorted(path for path in current_files if path not in old_manifest)
    modified = sorted(
        path
        for path, digest in current_files.items()
        if path in old_manifest and old_manifest[path] != digest
    )
    deleted = sorted(path for path in old_manifest if path not in current_files)
    unchanged = sorted(
        path
        for path, digest in current_files.items()
        if path in old_manifest and old_manifest[path] == digest
    )

    _write_manifest_atomic(manifest_path, current_files)
    return {
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "unchanged": unchanged,
    }
