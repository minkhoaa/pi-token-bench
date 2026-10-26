"""Incremental indexer for regular ``.txt`` files."""

import os
import stat
import tempfile
from pathlib import Path

from src.exceptions import IndexerError, ManifestWriteError
from src.hasher import compute_sha256
from src.manifest import format_manifest, load_manifest


def _write_manifest_atomically(manifest_path: Path, files: dict[str, str]) -> None:
    """Write a complete manifest beside its destination, then atomically replace it."""
    temporary_path: Path | None = None
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
            except FileNotFoundError:
                pass
            except OSError:
                # Do not mask the original write/replace failure during cleanup.
                pass


def sync_index(root: Path, manifest_path: Path) -> dict[str, list[str]]:
    """Synchronize the manifest with regular ``.txt`` files beneath ``root``."""
    # absolute() makes paths stable without resolving (and thereby following) a
    # symbolic-link root.
    root = Path(root).absolute()
    manifest_path = Path(manifest_path).absolute()

    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise IndexerError(f"Root path is not a directory: {root}") from exc
    if not stat.S_ISDIR(root_stat.st_mode):
        raise IndexerError(f"Root path is not a directory: {root}")

    # Let CorruptManifestError propagate: never replace or reset a bad manifest.
    old_manifest = load_manifest(manifest_path)
    current_files: dict[str, str] = {}

    # Iterative traversal avoids recursion-depth issues. DirEntry checks explicitly
    # avoid following either file or directory symlinks.
    pending_directories = [root]
    while pending_directories:
        directory = pending_directories.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending_directories.append(Path(entry.path))
                            continue
                        if not entry.name.endswith(".txt") or not entry.is_file(
                            follow_symlinks=False
                        ):
                            continue

                        file_path = Path(entry.path)
                        relative_path = file_path.relative_to(root).as_posix()
                        try:
                            current_files[relative_path] = compute_sha256(file_path)
                        except OSError:
                            # Files may disappear or become unreadable after listing.
                            continue
                    except OSError:
                        # Ignore entries that disappear while their metadata is read.
                        continue
        except OSError:
            # A directory may disappear or become inaccessible during traversal.
            continue

    added: list[str] = []
    modified: list[str] = []
    unchanged: list[str] = []
    deleted: list[str] = []

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
