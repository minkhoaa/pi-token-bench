"""
Inventory reservation service implementation.
NOTE: This implementation contains known defects in concurrency, idempotency,
validation, and cancellation semantics that must be diagnosed and repaired.
"""

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


def reserve(db_path: str, reservation_id: str, sku: str, quantity: int) -> dict:
    """
    Reserve inventory atomically and idempotently.
    DEFECTIVE: Lacks proper transaction locking, idempotency checks, and validation.
    """
    # Flawed validation: fails to reject booleans or negative values properly
    if not isinstance(reservation_id, str) or not reservation_id:
        raise InvalidReservationError("reservation_id must be a non-empty string")

    conn = get_connection(db_path)
    try:
        # Race condition: non-transactional read followed by write
        row = conn.execute("SELECT available_quantity FROM inventory WHERE sku = ?", (sku,)).fetchone()
        if row is None:
            raise SkuNotFoundError(f"SKU {sku} not found")

        avail = row["available_quantity"]
        if avail < quantity:
            raise InsufficientStockError(f"Requested {quantity}, available {avail}")

        now_iso = datetime.now(timezone.utc).isoformat()

        # Deduct without locking
        conn.execute(
            "UPDATE inventory SET available_quantity = available_quantity - ? WHERE sku = ?",
            (quantity, sku),
        )

        # Flawed insert: does not handle idempotency or replayed cancelled reservation
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
    finally:
        conn.close()


def cancel(db_path: str, reservation_id: str) -> dict:
    """
    Cancel reservation and restore inventory.
    DEFECTIVE: Restores inventory repeatedly if cancel is called multiple times.
    """
    if not isinstance(reservation_id, str) or not reservation_id:
        raise InvalidReservationError("reservation_id must be a non-empty string")

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT sku, quantity, status FROM reservations WHERE reservation_id = ?",
            (reservation_id,),
        ).fetchone()

        if row is None:
            raise ReservationNotFoundError(f"Reservation {reservation_id} not found")

        sku = row["sku"]
        qty = row["quantity"]
        now_iso = datetime.now(timezone.utc).isoformat()

        # Defect: restores stock regardless of whether it was already cancelled!
        conn.execute(
            "UPDATE inventory SET available_quantity = available_quantity + ? WHERE sku = ?",
            (qty, sku),
        )

        conn.execute(
            "UPDATE reservations SET status = 'cancelled', updated_at = ? WHERE reservation_id = ?",
            (now_iso, reservation_id),
        )

        return {
            "reservation_id": reservation_id,
            "sku": sku,
            "quantity": qty,
            "status": "cancelled",
        }
    finally:
        conn.close()
