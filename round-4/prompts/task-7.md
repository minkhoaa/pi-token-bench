# Task 7: Crash-Safe Event-Sourced Ledger

You are tasked with diagnosing and repairing an event-sourced financial ledger in the current working directory.

## Repository Overview
Inspect the existing repository structure:
- `src/ledger.py`: Core ledger implementation containing `Ledger`. **This is the ONLY file you are permitted to modify.**
- `src/journal.py`: Low-level append-only journal file implementation supporting JSON-line record framing, SHA-256 checksums, `fcntl.flock` file locking, `fsync` persistence, and trailing truncated record recovery.
- `src/models.py`: Data models including `Record`.
- `src/exceptions.py`: Custom domain exceptions (`LedgerError`, `InvalidAmountError`, `InsufficientFundsError`, `DuplicateTransactionError`, `ConflictingTransactionError`, `JournalCorruptionError`).
- `tests/`: Automated unit and concurrency test suites.

## Required Public API in `src/ledger.py`
```python
from pathlib import Path
from typing import Dict

class Ledger:
    def __init__(self, directory: Path):
        ...

    def credit(self, transaction_id: str, account: str, amount: int) -> dict:
        ...

    def transfer(self, transaction_id: str, source: str, target: str, amount: int) -> dict:
        ...

    def balance(self, account: str) -> int:
        ...

    def replay(self) -> Dict[str, int]:
        ...
```

## Objectives & Requirements
The current implementation in `src/ledger.py` is defective: it allows negative balances, accepts invalid amount types, violates idempotency, fails concurrency safety, and crashes on journal recovery.

Repair `src/ledger.py` to satisfy all of the following requirements:
1. **Balance Accounting & Initial Balance:**
   - Any account with no transaction history has a balance of `0`.
   - `credit(transaction_id, account, amount)` increases account balance by `amount` and returns a dict with `status="success"`, `command="credit"`, `transaction_id`, `account`, `amount`, and current `balance`.
   - `balance(account)` returns the integer balance.
2. **Atomic Transfer & Overdraft Prevention:**
   - `transfer(transaction_id, source, target, amount)` transfers funds from `source` to `target` atomically.
   - If `source` has insufficient funds (`balance < amount`), raise `InsufficientFundsError`.
   - The transaction must be rejected cleanly without writing any record to the journal or mutating balances.
   - Transferring between the same account (`source == target`) must be rejected.
   - Return dict with `status="success"`, `command="transfer"`, `transaction_id`, `source`, `target`, `amount`, `source_balance`, and `target_balance`.
3. **Strict Validation:**
   - `amount` must be a strictly positive integer (`amount > 0`).
   - Booleans (`True`/`False`), floating point numbers, non-integers, zero, or negative numbers must be rejected with `InvalidAmountError`.
   - `transaction_id`, `account`, `source`, and `target` must be non-empty strings.
4. **True Idempotency & Conflict Detection:**
   - Re-executing an operation with an identical `transaction_id` and identical parameters must be idempotent: it must return the previous transaction result without double-mutating balances or appending new journal entries.
   - Calling `credit` or `transfer` with an existing `transaction_id` but *different* parameters (or a different command) must raise `ConflictingTransactionError` without mutating state.
5. **Crash-Safe Journaling & Concurrency:**
   - All mutations must be persisted through the append-only `Journal` (`src/journal.py`) with record checksums and fsync durability.
   - Multiple concurrent processes or instances accessing the ledger directory must coordinate safely using `Journal.lock()` (`fcntl.flock`).
   - Replay (`replay()`) must accurately reconstruct balances across fresh `Ledger` instances from journal records.
   - Middle-record corruption or checksum tampering must raise `JournalCorruptionError`.
   - Incomplete trailing truncated records at EOF (e.g. from an interrupted process write) must be recovered cleanly without crashing replay.

## Strict Constraints
- **Modify ONLY `src/ledger.py`**. Do not modify tests, fixtures, `src/journal.py`, `src/models.py`, or `src/exceptions.py`.
- Python 3.11+ standard library only (`pathlib`, `fcntl`, `json`, `hashlib`, etc.).
- Do not install external packages or make network calls.
- Do not disable, delete, or weaken existing tests in `tests/`.

## Verification
- Run existing test suites: `python3 -m unittest discover -s tests`.
- Summarize the diagnosed issues, your fixes, and your test results concisely.
