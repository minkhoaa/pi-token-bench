"""Incremental file indexer implementation."""

import os
import stat
import tempfile
from pathlib import Path
from typing import Dict

from src.exceptions import IndexerError, ManifestWriteError
from src.hasher import compute_sha256
from src.manifest import format_manifest, load_manifest


def sync_index(root: Path, manifest_path: Path) -> dict:
    """
    Synchronize the manifest with current regular .txt files in root.
    """
    root = Path(root).absolute()
    manifest_path = Path(manifest_path).absolute()

    if root.is_symlink() or not root.is_dir():
        raise IndexerError(f"Root path is not a directory: {root}")

    # load_manifest errors, especially corruption, must abort before any write.
    old_manifest = load_manifest(manifest_path)

    current_files: Dict[str, str] = {}

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        directory = Path(dirpath)
        # Prune directory links explicitly, even though walk does not follow them.
        dirnames[:] = [name for name in dirnames if not (directory / name).is_symlink()]

        for fname in filenames:
            if not fname.endswith(".txt"):
                continue

            file_path = directory / fname
            try:
                if not stat.S_ISREG(file_path.lstat().st_mode):
                    continue
                rel_path = file_path.relative_to(root).as_posix()
                current_files[rel_path] = compute_sha256(file_path)
            except OSError:
                # Files may disappear or become inaccessible during the scan.
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
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{manifest_path.name}.", suffix=".tmp", dir=manifest_path.parent
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(format_manifest(current_files))
            f.flush()
            os.fsync(f.fileno())
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

    return {
        "added": sorted(added),
        "modified": sorted(modified),
        "deleted": sorted(deleted),
        "unchanged": sorted(unchanged),
    }
