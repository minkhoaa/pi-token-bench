"""Incremental file indexer implementation."""

import os
import tempfile
from pathlib import Path

from src.exceptions import IndexerError, ManifestWriteError
from src.hasher import compute_sha256
from src.manifest import format_manifest, load_manifest


def sync_index(root: Path, manifest_path: Path) -> dict:
    """Synchronize the manifest with regular .txt files under root."""
    root = Path(root).resolve()
    manifest_path = Path(manifest_path).absolute()

    if not root.is_dir():
        raise IndexerError(f"Root path is not a directory: {root}")

    # Do not alter anything if the existing manifest is corrupt.
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
                        if entry.is_dir(follow_symlinks=False):
                            directories.append(Path(entry.path))
                        elif entry.name.endswith(".txt") and entry.is_file(
                            follow_symlinks=False
                        ):
                            file_path = Path(entry.path)
                            rel_path = file_path.relative_to(root).as_posix()
                            try:
                                current_files[rel_path] = compute_sha256(file_path)
                            except OSError:
                                # The file may have disappeared or become unreadable.
                                continue
                    except OSError:
                        # Entries can disappear while the directory is being scanned.
                        continue
        except OSError:
            # A directory can disappear or become unreadable during traversal.
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

    manifest_tmp = None
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=manifest_path.parent,
            prefix=f".{manifest_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as manifest_file:
            manifest_tmp = Path(manifest_file.name)
            manifest_file.write(format_manifest(current_files))
            manifest_file.flush()
            os.fsync(manifest_file.fileno())
        os.replace(manifest_tmp, manifest_path)
        manifest_tmp = None
    except OSError as exc:
        raise ManifestWriteError(f"Failed to write manifest: {exc}") from exc
    finally:
        if manifest_tmp is not None:
            try:
                manifest_tmp.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise ManifestWriteError(
                    f"Failed to remove temporary manifest {manifest_tmp}: {exc}"
                ) from exc

    return {
        "added": sorted(added),
        "modified": sorted(modified),
        "deleted": sorted(deleted),
        "unchanged": sorted(unchanged),
    }
