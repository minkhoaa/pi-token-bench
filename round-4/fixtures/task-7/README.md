# Task 7: Crash-Safe Event-Sourced Ledger

## Overview
This repository contains an event-sourced ledger service backed by an append-only journal file.
The current implementation in `src/ledger.py` contains severe bugs and fails safety, concurrency, validation, and durability guarantees.

## Structure
- `src/ledger.py`: Main ledger implementation (**ONLY file you are allowed to edit**).
- `src/journal.py`: Low-level append-only journal file handling records, JSON framing, SHA-256 checksums, fsync, and process locking (`fcntl.flock`).
- `src/models.py`: Data models including `Record`.
- `src/exceptions.py`: Custom domain exceptions.
- `tests/`: Automated unit test suites.

## Running Tests
Run the test suite using standard unittest:
```bash
python3 -m unittest discover -s tests
```
