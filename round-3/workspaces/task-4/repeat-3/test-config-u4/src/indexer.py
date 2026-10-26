"""Incremental file indexer implementation."""

import os
import tempfile
from pathlib import Path

from src.exceptions import IndexerError, ManifestWriteError
from src.hasher import compute_sha256
from src.manifest import format_manifest, load_manifest


def sync_index(root: Path, manifest_path: Path) -> dict:
    """Synchronize the manifest with regular .txt files under ``root``."""
    root = Path(root).absolute()
    manifest_path = Path(manifest_path).absolute()

    if root.is_symlink() or not root.is_dir():
        raise IndexerError(f"Root path is not a directory: {root}")

    # Let corruption propagate before scanning or touching the manifest.
    old_manifest = load_manifest(manifest_path)
    current_files: dict[str, str] = {}

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        directory = Path(dirpath)
        # os.walk does not recurse into directory symlinks with followlinks=False;
        # prune them explicitly as well.
        dirnames[:] = [name for name in dirnames if not (directory / name).is_symlink()]

        for filename in filenames:
            if not filename.endswith(".txt"):
                continue

            file_path = directory / filename
            try:
                if file_path.is_symlink() or not file_path.is_file():
                    continue
                relative_path = file_path.relative_to(root).as_posix()
                current_files[relative_path] = compute_sha256(file_path)
            except OSError:
                # A file may disappear or become unreadable after directory listing.
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

    temporary_path = None
    descriptor = None
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{manifest_path.name}.",
            suffix=".tmp",
            dir=manifest_path.parent,
        )
        temporary_path = Path(temporary_name)
        stream = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = None  # The stream now owns the descriptor.
        with stream:
            stream.write(format_manifest(current_files))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, manifest_path)
        temporary_path = None
    except Exception as exc:
        raise ManifestWriteError(f"Failed to write manifest: {manifest_path}") from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
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
