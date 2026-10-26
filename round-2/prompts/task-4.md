# Task 4: Crash-Safe Incremental File Indexer Repair

You are tasked with diagnosing and repairing an incremental file indexer in the current working directory.

## Repository Overview
Inspect the existing repository structure:
- `src/indexer.py`: Main indexer module implementing `sync_index`. **This is the ONLY file you are permitted to modify.**
- `src/manifest.py`: Manifest serialization, loading, and schema validation.
- `src/hasher.py`: Streaming SHA-256 computation utility.
- `src/exceptions.py`: Domain exceptions (`CorruptManifestError`, `ManifestWriteError`, `IndexerError`).
- `tests/`: Automated unit and symlink test suites.

## Objectives & Requirements
The current implementation in `src/indexer.py` contains critical defects in symlink traversal, memory usage, manifest persistence, and corruption recovery.

Repair `src/indexer.py` so that it satisfies all of the following requirements:
1. **Target Files Only:** Recursively discover regular `.txt` files under `root`. Ignore non-txt files.
2. **Symlink Safety:**
   - Strictly ignore all symbolic links (symlinks pointing to files, symlinks pointing to directories, symlinks pointing outside the root).
   - Never follow recursive symlink loops or suffer infinite recursion.
3. **Normalized POSIX Paths:**
   - All relative paths stored in the manifest and returned in change lists must use normalized POSIX separators (`/`) regardless of platform.
4. **Incremental Streaming Hashing:**
   - Compute SHA-256 incrementally in chunks (e.g. 64 KB) using `compute_sha256`. Do not read entire large files into memory.
5. **Change Detection:**
   - Compare current file hashes against the loaded manifest. Correctly classify each path into one of:
     - `added`: newly created `.txt` files.
     - `modified`: existing files whose content SHA-256 has changed. (Files with updated timestamps but identical content must remain `unchanged`).
     - `deleted`: previously indexed files no longer present.
     - `unchanged`: existing files whose content SHA-256 has not changed.
   - Return dictionary with all four change lists, sorted lexicographically:
     ```python
     {
         "added": ["..."],
         "modified": ["..."],
         "deleted": ["..."],
         "unchanged": ["..."],
     }
     ```
6. **Atomic Manifest Persistence:**
   - Write the manifest atomically (e.g. write to a temporary file in the same directory as `manifest_path` and use `os.replace`) so crashes or partial writes never corrupt the manifest on disk.
   - Ensure temporary files are cleaned up and never leak into the repository.
7. **Corruption Handling:**
   - If an existing manifest is corrupted or contains an invalid schema, `load_manifest` raises `CorruptManifestError`.
   - `sync_index` must propagate `CorruptManifestError` and must NOT overwrite, delete, or reset the corrupted file.
8. **Robustness:**
   - Safely handle empty directories and Unicode filenames (accents, Asian scripts, spaces).
   - Gracefully handle files disappearing between directory listing and hashing.

## Strict Constraints
- **Modify ONLY `src/indexer.py`**. Do not modify tests, `src/manifest.py`, `src/hasher.py`, or `src/exceptions.py`.
- Python 3.11+ standard library only (`pathlib`, `os`, `hashlib`, `tempfile`, etc.).
- Do not install external packages or make network calls.
- Do not disable, delete, or weaken existing tests in `tests/`.

## Verification
- Run existing test suites: `python -m unittest discover -s tests`.
- Add any focused temporary checks if needed, but remove temporary test files before concluding.
- When finished, summarize the diagnosed issues, your fixes, and your test results concisely.
