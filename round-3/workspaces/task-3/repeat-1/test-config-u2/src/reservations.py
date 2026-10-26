"""
Inventory reservation service implementation.
NOTE: The original implementation contained known defects in concurrency, idempotency,
validation, and cancellation semantics that required diagnosis and repair. Those defects
are repaired in this implementation.
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


def get_available(db_path: str, sku: str) -> int:
    """Return available inventory quantity for SKU."""
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT available_quantity FROM inventory WHERE sku = ?", (sku,)).fetchone()
        if row is None:
            raise SkuNotFoundError(f"SKU {sku} not found")
        return int(row["available_quantity"])
    finally:
        conn.close()


def _validate_reservation_id(reservation_id: str) -> None:
    if (
        not isinstance(reservation_id, str)
        or not reservation_id
        or reservation_id != reservation_id.strip()
    ):
        raise InvalidReservationError("reservation_id must be a non-empty, stripped string")


def _is_lock_error(error: sqlite3.OperationalError) -> bool:
    code = getattr(error, "sqlite_errorcode", None)
    if code is not None and (code & 0xFF) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        return True
    message = str(error).lower()
    return "locked" in message or "busy" in message


def _rollback(conn: sqlite3.Connection | None) -> None:
    if conn is not None and conn.in_transaction:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            # Closing the connection below also rolls back any still-open transaction.
            pass


def _run_write_transaction(db_path: str, operation) -> dict:
    """Run one serialized write transaction, retrying transient SQLite lock errors."""
    retries = 8
    for attempt in range(retries):
        conn = None
        try:
            conn = get_connection(db_path, timeout=30.0)
            conn.execute("BEGIN IMMEDIATE")
            result = operation(conn)
            conn.execute("COMMIT")
            return result
        except sqlite3.OperationalError as error:
            _rollback(conn)
            if not _is_lock_error(error) or attempt == retries - 1:
                raise
            time.sleep(min(0.01 * (2**attempt), 0.25))
        except BaseException:
            _rollback(conn)
            raise
        finally:
            if conn is not None:
                conn.close()
    raise RuntimeError("unreachable")


def reserve(db_path: str, reservation_id: str, sku: str, quantity: int) -> dict:
    """
    Reserve inventory atomically and idempotently.
    DEFECTIVE (repaired below): Lacks proper transaction locking, idempotency checks, and validation.
    """
    # Flawed validation: fails to reject booleans or negative values properly (fixed by validation below).
    _validate_reservation_id(reservation_id)
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise InvalidReservationError("quantity must be a positive integer")

    def reserve_in_transaction(conn: sqlite3.Connection) -> dict:
        # Race condition: non-transactional read followed by write (fixed by BEGIN IMMEDIATE below).
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
                raise InvalidReservationError(f"Reservation {reservation_id} is cancelled")
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

        available = row["available_quantity"]
        if available < quantity:
            raise InsufficientStockError(f"Requested {quantity}, available {available}")

        now_iso = datetime.now(timezone.utc).isoformat()
        # Deduct without locking (now protected by BEGIN IMMEDIATE).
        updated = conn.execute(
            """
            UPDATE inventory
            SET available_quantity = available_quantity - ?
            WHERE sku = ? AND available_quantity >= ?
            """,
            (quantity, sku, quantity),
        )
        if updated.rowcount != 1:
            raise InsufficientStockError(f"Requested {quantity}, available {available}")

        # Flawed insert: does not handle idempotency or replayed cancelled reservation (handled above).
        conn.execute(
            """
            INSERT INTO reservations (reservation_id, sku, quantity, status, created_at, updated_at)
            VALUES (?, ?, ?, 'active', ?, ?)
            """,
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
    """
    Cancel a reservation and restore inventory.
    DEFECTIVE (repaired below): Restores inventory repeatedly if cancel is called multiple times.
    """
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
        # Defect: restores stock regardless of whether it was already cancelled (fixed by this guard).
        if row["status"] == "active":
            now_iso = datetime.now(timezone.utc).isoformat()
            updated = conn.execute(
                """
                UPDATE reservations
                SET status = 'cancelled', updated_at = ?
                WHERE reservation_id = ? AND status = 'active'
                """,
                (now_iso, reservation_id),
            )
            if updated.rowcount != 1:
                raise RuntimeError(f"Reservation {reservation_id} state changed unexpectedly")
            restored = conn.execute(
                "UPDATE inventory SET available_quantity = available_quantity + ? WHERE sku = ?",
                (quantity, sku),
            )
            if restored.rowcount != 1:
                raise SkuNotFoundError(f"SKU {sku} not found")

        return {
            "reservation_id": reservation_id,
            "sku": sku,
            "quantity": quantity,
            "status": "cancelled",
        }

    return _run_write_transaction(db_path, cancel_in_transaction)
