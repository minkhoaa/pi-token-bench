"""
Inventory reservation service implementation.
NOTE: This implementation contains known defects in concurrency, idempotency,
validation, and cancellation semantics that must be diagnosed and repaired.
"""

import sqlite3
import time
from datetime import datetime, timezone

from src.db import get_connection
from src.exceptions import (
    InsufficientStockError,
    InvalidReservationError,
    ReservationNotFoundError,
    SkuNotFoundError,
)


_LOCK_RETRIES = 7


def _is_database_busy(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def _open_connection(db_path: str) -> sqlite3.Connection:
    """Open a WAL-configured connection, retrying transient SQLite lock errors."""
    for attempt in range(_LOCK_RETRIES):
        try:
            return get_connection(db_path)
        except sqlite3.OperationalError as exc:
            if not _is_database_busy(exc) or attempt == _LOCK_RETRIES - 1:
                raise
            time.sleep(min(0.01 * (2**attempt), 0.25))
    raise RuntimeError("unreachable")


def _rollback(conn: sqlite3.Connection | None) -> None:
    if conn is not None and conn.in_transaction:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            # Preserve the exception that caused the rollback.
            pass


def _run_write_transaction(db_path: str, operation):
    """Run an operation under an immediate write lock, with bounded busy retries."""
    for attempt in range(_LOCK_RETRIES):
        conn = None
        try:
            conn = _open_connection(db_path)
            conn.execute("BEGIN IMMEDIATE")
            result = operation(conn)
            conn.execute("COMMIT")
            return result
        except sqlite3.OperationalError as exc:
            _rollback(conn)
            if not _is_database_busy(exc) or attempt == _LOCK_RETRIES - 1:
                raise
            time.sleep(min(0.01 * (2**attempt), 0.25))
        except BaseException:
            _rollback(conn)
            raise
        finally:
            if conn is not None:
                conn.close()
    raise RuntimeError("unreachable")


def _validate_reservation_id(reservation_id: str) -> None:
    if (
        not isinstance(reservation_id, str)
        or not reservation_id
        or reservation_id != reservation_id.strip()
    ):
        raise InvalidReservationError("reservation_id must be a non-empty, stripped string")


def get_available(db_path: str, sku: str) -> int:
    """Return available inventory quantity for SKU."""
    conn = _open_connection(db_path)
    try:
        row = conn.execute("SELECT available_quantity FROM inventory WHERE sku = ?", (sku,)).fetchone()
        if row is None:
            raise SkuNotFoundError(f"SKU {sku} not found")
        return int(row["available_quantity"])
    finally:
        conn.close()


def reserve(db_path: str, reservation_id: str, sku: str, quantity: int) -> dict:
    """Atomically reserve stock, with idempotency for active reservations."""
    _validate_reservation_id(reservation_id)
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise InvalidReservationError("quantity must be a positive integer")

    def reserve_in_transaction(conn: sqlite3.Connection) -> dict:
        existing = conn.execute(
            "SELECT sku, quantity, status FROM reservations WHERE reservation_id = ?",
            (reservation_id,),
        ).fetchone()
        if existing is not None:
            if existing["sku"] != sku or existing["quantity"] != quantity:
                raise InvalidReservationError(
                    f"Reservation {reservation_id} already exists with different parameters"
                )
            if existing["status"] != "active":
                raise InvalidReservationError(
                    f"Reservation {reservation_id} has already been cancelled"
                )
            return {
                "reservation_id": reservation_id,
                "sku": existing["sku"],
                "quantity": existing["quantity"],
                "status": "active",
            }

        row = conn.execute(
            "SELECT available_quantity FROM inventory WHERE sku = ?", (sku,)
        ).fetchone()
        if row is None:
            raise SkuNotFoundError(f"SKU {sku} not found")

        available = int(row["available_quantity"])
        if available < quantity:
            raise InsufficientStockError(f"Requested {quantity}, available {available}")

        # Keep the non-negative condition in the UPDATE as a final guard as well
        # as holding the write lock for the complete read/modify/insert sequence.
        updated = conn.execute(
            """UPDATE inventory
               SET available_quantity = available_quantity - ?
               WHERE sku = ? AND available_quantity >= ?""",
            (quantity, sku, quantity),
        )
        if updated.rowcount != 1:
            raise InsufficientStockError(f"Requested {quantity}, available {available}")

        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO reservations
               (reservation_id, sku, quantity, status, created_at, updated_at)
               VALUES (?, ?, ?, 'active', ?, ?)""",
            (reservation_id, sku, quantity, now_iso, now_iso),
        )
        return {
            "reservation_id": reservation_id,
            "sku": sku,
            "quantity": quantity,
            "status": "active",
        }

    return _run_write_transaction(db_path, reserve_in_transaction)


def cancel(db_path: str, reservation_id: str) -> dict:
    """Cancel a reservation and restore its inventory exactly once."""
    _validate_reservation_id(reservation_id)

    def cancel_in_transaction(conn: sqlite3.Connection) -> dict:
        row = conn.execute(
            "SELECT sku, quantity, status FROM reservations WHERE reservation_id = ?",
            (reservation_id,),
        ).fetchone()
        if row is None:
            raise ReservationNotFoundError(f"Reservation {reservation_id} not found")

        sku = row["sku"]
        quantity = row["quantity"]
        if row["status"] == "active":
            now_iso = datetime.now(timezone.utc).isoformat()
            restored = conn.execute(
                "UPDATE inventory SET available_quantity = available_quantity + ? WHERE sku = ?",
                (quantity, sku),
            )
            if restored.rowcount != 1:
                raise SkuNotFoundError(f"SKU {sku} not found")
            updated = conn.execute(
                """UPDATE reservations SET status = 'cancelled', updated_at = ?
                   WHERE reservation_id = ? AND status = 'active'""",
                (now_iso, reservation_id),
            )
            if updated.rowcount != 1:
                raise InvalidReservationError(f"Could not cancel reservation {reservation_id}")
        elif row["status"] != "cancelled":
            raise InvalidReservationError(f"Reservation {reservation_id} has an invalid status")

        return {
            "reservation_id": reservation_id,
            "sku": sku,
            "quantity": quantity,
            "status": "cancelled",
        }

    return _run_write_transaction(db_path, cancel_in_transaction)
