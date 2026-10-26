"""Incremental file indexer implementation."""

import os
import tempfile
from pathlib import Path

from src.exceptions import IndexerError, ManifestWriteError
from src.hasher import compute_sha256
from src.manifest import format_manifest, load_manifest


def sync_index(root: Path, manifest_path: Path) -> dict:
    """Synchronize the manifest with regular ``.txt`` files beneath ``root``."""
    root = Path(root).resolve()
    # Do not resolve the final manifest component: os.replace should replace a
    # manifest symlink itself rather than write through it to its target.
    manifest_path = Path(os.path.abspath(manifest_path))

    if not root.is_dir():
        raise IndexerError(f"Root path is not a directory: {root}")

    # Corruption is fatal; never replace an existing manifest that failed validation.
    old_manifest = load_manifest(manifest_path)
    current_files: dict[str, str] = {}

    # Walk explicitly so neither directory nor file symlinks are followed.
    pending_dirs = [root]
    while pending_dirs:
        directory = pending_dirs.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending_dirs.append(Path(entry.path))
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
                            # The file may disappear or become unreadable after scanning.
                            continue
                    except OSError:
                        # Ignore entries that disappear or change during traversal.
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

    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=manifest_path.parent,
                prefix=f".{manifest_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                temp_file.write(format_manifest(current_files))
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, manifest_path)
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass
    except OSError as exc:
        raise ManifestWriteError(f"Failed to write manifest: {exc}") from exc

    return {
        "added": sorted(added),
        "modified": sorted(modified),
        "deleted": sorted(deleted),
        "unchanged": sorted(unchanged),
    }
