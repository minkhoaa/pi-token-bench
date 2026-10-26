"""Incremental file indexer implementation."""

import os
import tempfile
from pathlib import Path

from src.exceptions import IndexerError, ManifestWriteError
from src.hasher import compute_sha256
from src.manifest import format_manifest, load_manifest


def _iter_txt_files(root: Path):
    """Yield regular .txt files without traversing symbolic links."""
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
            # An unreadable or removed directory should not abort the full index.
            continue


def _write_manifest_atomic(manifest_path: Path, files: dict[str, str]) -> None:
    """Write a complete manifest beside its destination, then replace atomically."""
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
        raise ManifestWriteError(
            f"Failed to write manifest at {manifest_path}: {exc}"
        ) from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                # Preserve the original write/replace error if cleanup also fails.
                pass


def sync_index(root: Path, manifest_path: Path) -> dict:
    """Index regular .txt files under root and atomically update the manifest."""
    root = Path(root).resolve()
    manifest_path = Path(manifest_path).resolve()

    if not root.is_dir():
        raise IndexerError(f"Root path is not a directory: {root}")

    # Do not recover by replacing a corrupt manifest: preserve it and fail closed.
    old_manifest = load_manifest(manifest_path)

    current_files: dict[str, str] = {}
    for file_path in _iter_txt_files(root):
        try:
            digest = compute_sha256(file_path)
        except OSError:
            # A file may disappear or become unreadable after it was discovered.
            continue
        current_files[file_path.relative_to(root).as_posix()] = digest

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

    _write_manifest_atomic(manifest_path, current_files)

    return {
        "added": sorted(added),
        "modified": sorted(modified),
        "deleted": sorted(deleted),
        "unchanged": sorted(unchanged),
    }
