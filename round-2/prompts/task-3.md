# Task 3: Concurrent Inventory Reservation Service Repair

You are tasked with diagnosing and repairing an inventory reservation service backed by SQLite in the current working directory.

## Repository Overview
Inspect the existing repository structure:
- `src/reservations.py`: Core service implementation containing `reserve`, `cancel`, and `get_available`. **This is the ONLY file you are permitted to modify.**
- `src/db.py`: SQLite connection factory and schema initialization.
- `src/exceptions.py`: Custom domain exceptions (`SkuNotFoundError`, `InsufficientStockError`, `ReservationNotFoundError`, `InvalidReservationError`).
- `src/models.py`: Data models and status definitions.
- `schema.sql`: SQLite database schema.
- `tests/`: Automated unit and concurrency test suites.

## Objectives & Requirements
The current implementation in `src/reservations.py` contains critical defects in transaction isolation, concurrency, idempotency, cancellation recovery, and validation.

Repair `src/reservations.py` so that it satisfies all of the following requirements:
1. **Atomic Stock Reservation:** Reserve stock atomically. Never permit available inventory to become negative or oversubscribed under concurrent reservations.
2. **True Idempotency:**
   - Calling `reserve()` with an identical `reservation_id` and identical parameters (`sku`, `quantity`) must be idempotent: it must return the existing active reservation without deducting inventory a second time.
   - Calling `reserve()` with an existing `reservation_id` but *different* parameters (`sku` or `quantity`) must raise `InvalidReservationError` without mutating state.
3. **Reopening Prevention:** A reservation that has been cancelled must NEVER be reopened by replaying its `reservation_id` (must raise `InvalidReservationError`).
4. **Safe Cancellation:**
   - Calling `cancel()` on an active reservation restores the reserved quantity back to available inventory.
   - Repeated calls to `cancel()` on the same reservation must be safe and idempotent, returning the cancelled state without restoring inventory more than once.
   - Attempting to cancel a non-existent reservation must raise `ReservationNotFoundError`.
5. **Concurrency Safety:**
   - Multiple concurrent threads or processes reserving against the same database must serialize writes safely without race conditions or `database is locked` operational failures.
   - Use proper transaction control (e.g. `BEGIN IMMEDIATE` / write transactions) and WAL configuration.
6. **Input Validation:**
   - `quantity` must be a positive integer (`quantity > 0`). Booleans (`True`/`False`), floats, non-integers, zero, or negative numbers must be rejected with `InvalidReservationError` (or `ValueError`).
   - `reservation_id` must be a non-empty, stripped string.
   - Missing SKUs must raise `SkuNotFoundError`.
   - Insufficient available stock must raise `InsufficientStockError`.
7. **Clean Rollbacks & Resource Management:**
   - If any failure occurs during a reservation or cancellation operation, the transaction must roll back cleanly.
   - Connections must always be closed cleanly without resource leaks.

## Strict Constraints
- **Modify ONLY `src/reservations.py`**. Do not modify tests, fixtures, `src/db.py`, `src/exceptions.py`, or `schema.sql`.
- Python 3.11+ standard library only (`sqlite3`, `datetime`, `threading`, etc.).
- Do not install external packages or make network calls.
- Do not disable, delete, or weaken existing tests in `tests/`.

## Verification
- Run existing test suites: `python -m unittest discover -s tests`.
- Add any focused temporary checks if needed, but remove temporary test files before concluding.
- When finished, summarize the diagnosed issues, your fixes, and your test results concisely.
