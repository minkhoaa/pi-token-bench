# Concurrent Inventory Reservation Service

A lightweight, SQLite-backed inventory reservation service providing atomic, idempotent reservation and cancellation semantics.

## Architecture

- `src/reservations.py`: Core API implementation (`reserve`, `cancel`, `get_available`). **This is the designated module for bug fixes and improvements.**
- `src/db.py`: SQLite connection factory with WAL mode and busy timeout settings, plus schema initialization.
- `src/exceptions.py`: Domain exceptions (`SkuNotFoundError`, `InsufficientStockError`, `ReservationNotFoundError`, `InvalidReservationError`).
- `src/models.py`: Data models and `ReservationStatus` constants.
- `schema.sql`: Table definitions for `inventory` and `reservations`.
- `scripts/bootstrap_db.py`: CLI tool for initializing databases with initial stock.
- `tests/`: Automated unit and concurrency test suites.

## API Contract

### `reserve(db_path: str, reservation_id: str, sku: str, quantity: int) -> dict`
- Atomically reserves `quantity` units of `sku`.
- Returns dictionary:
  ```json
  {
    "reservation_id": "...",
    "sku": "...",
    "quantity": 10,
    "status": "active"
  }
  ```
- **Idempotency:** Replaying an identical `reservation_id` with identical `sku` and `quantity` must return the existing active reservation without double-deducting stock.
- **Conflict Handling:** Reusing an existing `reservation_id` with different parameters must raise `InvalidReservationError` without mutating state.
- **Reopen Prevention:** A cancelled reservation cannot be re-opened by re-reserving its ID (must raise `InvalidReservationError`).
- **Validation:** `quantity` must be a positive integer (`> 0`, not boolean). `sku` must exist. `reservation_id` must be a non-empty string.
- **Concurrency:** Concurrent requests must serialize safely without oversubscription or lock errors.

### `cancel(db_path: str, reservation_id: str) -> dict`
- Cancels an active reservation and restores the reserved quantity to available stock.
- Returns dictionary:
  ```json
  {
    "reservation_id": "...",
    "sku": "...",
    "quantity": 10,
    "status": "cancelled"
  }
  ```
- **Repeated Cancellation:** Calling `cancel` on an already-cancelled reservation must be safe and idempotent, returning the cancelled state without restoring stock a second time.
- If `reservation_id` does not exist, raises `ReservationNotFoundError`.

### `get_available(db_path: str, sku: str) -> int`
- Returns current unreserved available stock.
- If `sku` does not exist, raises `SkuNotFoundError`.

## Running Tests

```bash
python -m unittest discover -s tests
```
