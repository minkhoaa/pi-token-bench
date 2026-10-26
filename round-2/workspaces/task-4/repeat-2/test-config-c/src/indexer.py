"""Incremental file indexer implementation."""

import os
import stat
import tempfile
from pathlib import Path

from src.exceptions import IndexerError, ManifestWriteError
from src.hasher import compute_sha256
from src.manifest import format_manifest, load_manifest


def _write_manifest_atomically(manifest_path: Path, contents: str) -> None:
    """Replace the manifest only after a complete same-directory write."""
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
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(contents)
            temp_file.flush()
            os.fsync(temp_file.fileno())

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
            except OSError as exc:
                raise ManifestWriteError(
                    f"Failed to remove temporary manifest {temp_path}: {exc}"
                ) from exc


def sync_index(root: Path, manifest_path: Path) -> dict:
    """Synchronize the manifest with regular .txt files found beneath root."""
    root = Path(root).resolve()
    manifest_path = Path(manifest_path).resolve()

    if not root.is_dir():
        raise IndexerError(f"Root path is not a directory: {root}")

    # A corrupt manifest must abort before any write can replace it.
    old_manifest = load_manifest(manifest_path)
    current_files: dict[str, str] = {}

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        directory = Path(dirpath)
        # os.walk does not recurse through directory symlinks with followlinks=False;
        # prune them explicitly as well so they are never considered for traversal.
        dirnames[:] = [name for name in dirnames if not (directory / name).is_symlink()]

        for filename in filenames:
            if not filename.endswith(".txt"):
                continue

            file_path = directory / filename
            try:
                file_stat = file_path.lstat()
            except OSError:
                # The file may disappear after its directory was listed.
                continue
            if not stat.S_ISREG(file_stat.st_mode):
                continue

            try:
                current_files[file_path.relative_to(root).as_posix()] = compute_sha256(file_path)
            except OSError:
                # A file may disappear or become unreadable between lstat and hashing.
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

    _write_manifest_atomically(manifest_path, format_manifest(current_files))

    return {
        "added": sorted(added),
        "modified": sorted(modified),
        "deleted": sorted(deleted),
        "unchanged": sorted(unchanged),
    }
