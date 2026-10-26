"""Crash-safe incremental indexer for regular ``.txt`` files."""

import os
import stat
import tempfile
from pathlib import Path

from src.exceptions import IndexerError, ManifestWriteError
from src.hasher import compute_sha256
from src.manifest import format_manifest, load_manifest


def _write_manifest_atomically(manifest_path: Path, files: dict[str, str]) -> None:
    """Write a complete manifest beside the destination, then atomically replace it."""
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
            temporary_file.write(format_manifest(files))
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_path, manifest_path)
        temporary_path = None
    except OSError as exc:
        raise ManifestWriteError(f"Failed to write manifest {manifest_path}: {exc}") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def sync_index(root: Path, manifest_path: Path) -> dict:
    """Synchronize the manifest with regular ``.txt`` files beneath ``root``."""
    root = Path(root)
    if root.is_symlink():
        raise IndexerError(f"Root path is not a directory: {root}")
    root = root.resolve()
    manifest_path = Path(manifest_path).resolve()

    if not root.is_dir():
        raise IndexerError(f"Root path is not a directory: {root}")

    # Loading happens before scanning or writing so corruption is propagated and
    # the original manifest is left untouched.
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
                        entry_stat = entry.stat(follow_symlinks=False)
                        if stat.S_ISDIR(entry_stat.st_mode):
                            directories.append(Path(entry.path))
                        elif (
                            entry.name.endswith(".txt")
                            and stat.S_ISREG(entry_stat.st_mode)
                        ):
                            file_path = Path(entry.path)
                            relative_path = file_path.relative_to(root).as_posix()
                            try:
                                current_files[relative_path] = compute_sha256(file_path)
                            except OSError:
                                # Files may disappear or become unreadable after listing.
                                continue
                    except OSError:
                        # Ignore entries that disappear or become inaccessible mid-scan.
                        continue
        except OSError:
            # An unreadable or removed directory contributes no discoverable files.
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
