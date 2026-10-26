# Crash-Safe Incremental File Indexer

A lightweight, robust filesystem indexer that scans regular `.txt` files and maintains an incremental SHA-256 manifest.

## Architecture

- `src/indexer.py`: Main indexer module implementing `sync_index`. **This is the designated module for bug fixes and improvements.**
- `src/manifest.py`: Manifest serialization, deserialization, and schema validation.
- `src/hasher.py`: Streaming SHA-256 computation utility.
- `src/exceptions.py`: Domain exceptions (`CorruptManifestError`, `ManifestWriteError`, `IndexerError`).
- `tests/`: Automated unit and symlink test suites.

## API Specification

### `sync_index(root: Path, manifest_path: Path) -> dict`

Synchronizes the manifest located at `manifest_path` with all regular `.txt` files under directory `root`.

#### Return Contract
Returns a dictionary with four lexicographically sorted lists of relative POSIX paths:
```python
{
    "added": ["docs/new.txt", "notes.txt"],
    "modified": ["readme.txt"],
    "deleted": ["old.txt"],
    "unchanged": ["archive/log.txt"],
}
```

#### Manifest Format
Stored as valid UTF-8 JSON at `manifest_path`:
```json
{
  "version": 1,
  "files": {
    "docs/new.txt": "a6c2f9...",
    "notes.txt": "8b1e4a..."
  }
}
```

#### Requirements & Constraints
1. **File Type:** Only regular files ending in `.txt` are indexed. Ignore non-txt files.
2. **Symlink Safety:** All symbolic links (file symlinks, directory symlinks, links outside root) must be strictly ignored. Symlink loops must never cause infinite recursion.
3. **Path Normalization:** Relative paths must be normalized with POSIX separators (`/`) regardless of operating system.
4. **Streaming Hashing:** SHA-256 hashes must be calculated incrementally using chunked streaming (do not load entire files into memory).
5. **Atomic Persistence:** Manifest writes must be atomic (e.g. write to a temporary file in the same directory, then atomically replace `manifest_path`) so partial writes or crashes never corrupt the manifest.
6. **Corruption Handling:** If an existing manifest is corrupted or invalid JSON, raise `CorruptManifestError` and preserve the corrupted manifest file without overwriting it.
7. **Cleanliness:** No temporary files or unexpected artifacts may be left behind.
8. **Sorted Outputs:** All returned lists must be sorted in ascending lexicographical order.

## Running Tests

```bash
python -m unittest discover -s tests
```
