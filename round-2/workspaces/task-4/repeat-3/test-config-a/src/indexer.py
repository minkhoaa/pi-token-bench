"""Incremental file indexer implementation."""

import os
import stat
import tempfile
from pathlib import Path
from typing import Dict, Iterator

from src.exceptions import IndexerError, ManifestWriteError
from src.hasher import compute_sha256
from src.manifest import format_manifest, load_manifest


def _iter_txt_files(root: Path) -> Iterator[Path]:
    """Yield regular .txt files below root without traversing symbolic links."""
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.name.endswith(".txt") and entry.is_file(
                            follow_symlinks=False
                        ):
                            yield Path(entry.path)
                    except OSError:
                        # Entries can disappear or become inaccessible during a scan.
                        continue
        except OSError:
            # An unreadable or removed directory contributes no current files.
            continue


def _write_manifest_atomically(manifest_path: Path, files: Dict[str, str]) -> None:
    """Write a complete manifest beside its destination, then atomically replace it."""
    temporary_path = None
    temporary_fd = None
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_fd, temporary_name = tempfile.mkstemp(
            prefix=f".{manifest_path.name}.",
            suffix=".tmp",
            dir=manifest_path.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(temporary_fd, "w", encoding="utf-8", newline="\n") as stream:
            temporary_fd = None  # fdopen owns and closes the descriptor from here on.
            stream.write(format_manifest(files))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, manifest_path)
        temporary_path = None
    except OSError as exc:
        raise ManifestWriteError(f"Failed to write manifest {manifest_path}: {exc}") from exc
    finally:
        if temporary_fd is not None:
            try:
                os.close(temporary_fd)
            except OSError:
                pass
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise ManifestWriteError(
                    f"Failed to remove temporary manifest {temporary_path}: {exc}"
                ) from exc


def sync_index(root: Path, manifest_path: Path) -> dict:
    """Synchronize the manifest with regular .txt files found recursively under root."""
    root = Path(root)
    if root.is_symlink():
        raise IndexerError(f"Root path must not be a symbolic link: {root}")
    root = root.resolve()
    manifest_path = Path(manifest_path).resolve()

    if not root.is_dir():
        raise IndexerError(f"Root path is not a directory: {root}")

    # Let corruption errors propagate before scanning or writing anything. In particular,
    # never replace or reset a manifest that failed validation.
    old_manifest = load_manifest(manifest_path)

    current_files: Dict[str, str] = {}
    for file_path in _iter_txt_files(root):
        try:
            # Recheck without following links immediately before handing the path to the
            # streaming hasher. This also skips files removed after directory enumeration.
            file_stat = os.stat(file_path, follow_symlinks=False)
            if not stat.S_ISREG(file_stat.st_mode):
                continue
            digest = compute_sha256(file_path)
        except OSError:
            # A file may disappear (or become unreadable) between listing and hashing.
            continue

        relative_path = file_path.relative_to(root).as_posix()
        current_files[relative_path] = digest

    old_paths = set(old_manifest)
    current_paths = set(current_files)
    added = sorted(current_paths - old_paths)
    deleted = sorted(old_paths - current_paths)
    modified = sorted(
        path
        for path in current_paths & old_paths
        if current_files[path] != old_manifest[path]
    )
    unchanged = sorted(
        path
        for path in current_paths & old_paths
        if current_files[path] == old_manifest[path]
    )

    _write_manifest_atomically(manifest_path, current_files)

    return {
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "unchanged": unchanged,
    }
