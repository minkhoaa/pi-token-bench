"""SQLite-backed inventory reservation service."""

import sqlite3
from datetime import datetime, timezone

from src.db import get_connection
from src.exceptions import (
    InsufficientStockError,
    InvalidReservationError,
    ReservationNotFoundError,
    SkuNotFoundError,
)


def _validate_reservation_id(reservation_id: str) -> None:
    if (
        not isinstance(reservation_id, str)
        or not reservation_id
        or reservation_id != reservation_id.strip()
    ):
        raise InvalidReservationError("reservation_id must be a non-empty, stripped string")


def _rollback(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass


def get_available(db_path: str, sku: str) -> int:
    """Return available inventory quantity for SKU."""
    conn = get_connection(db_path, timeout=30.0)
    try:
        row = conn.execute("SELECT available_quantity FROM inventory WHERE sku = ?", (sku,)).fetchone()
        if row is None:
            raise SkuNotFoundError(f"SKU {sku} not found")
        return int(row["available_quantity"])
    finally:
        conn.close()


def reserve(db_path: str, reservation_id: str, sku: str, quantity: int) -> dict:
    """Atomically reserve stock, with reservation-ID idempotency."""
    _validate_reservation_id(reservation_id)
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise InvalidReservationError("quantity must be a positive integer")
    quantity = int(quantity)

    conn = get_connection(db_path, timeout=30.0)
    try:
        # Acquire the write lock before reading either reservation or inventory.
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT sku, quantity, status FROM reservations WHERE reservation_id = ?",
            (reservation_id,),
        ).fetchone()
        if existing is not None:
            if (
                existing["sku"] != sku
                or existing["quantity"] != quantity
                or existing["status"] != "active"
            ):
                raise InvalidReservationError(
                    f"Reservation {reservation_id} already exists with different or inactive parameters"
                )
            result = {
                "reservation_id": reservation_id,
                "sku": existing["sku"],
                "quantity": existing["quantity"],
                "status": "active",
            }
            conn.execute("COMMIT")
            return result

        row = conn.execute(
            "SELECT available_quantity FROM inventory WHERE sku = ?", (sku,)
        ).fetchone()
        if row is None:
            raise SkuNotFoundError(f"SKU {sku} not found")

        available = row["available_quantity"]
        if available < quantity:
            raise InsufficientStockError(f"Requested {quantity}, available {available}")

        # Keep the stock constraint in the write itself as an additional guard.
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

        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO reservations (reservation_id, sku, quantity, status, created_at, updated_at)
            VALUES (?, ?, ?, 'active', ?, ?)
            """,
            (reservation_id, sku, quantity, now_iso, now_iso),
        )
        conn.execute("COMMIT")
        return {
            "reservation_id": reservation_id,
            "sku": sku,
            "quantity": quantity,
            "status": "active",
        }
    except BaseException:
        _rollback(conn)
        raise
    finally:
        conn.close()


def cancel(db_path: str, reservation_id: str) -> dict:
    """Cancel an active reservation and restore its stock exactly once."""
    _validate_reservation_id(reservation_id)

    conn = get_connection(db_path, timeout=30.0)
    try:
        conn.execute("BEGIN IMMEDIATE")
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
            changed = conn.execute(
                """
                UPDATE reservations
                SET status = 'cancelled', updated_at = ?
                WHERE reservation_id = ? AND status = 'active'
                """,
                (now_iso, reservation_id),
            )
            if changed.rowcount == 1:
                restored = conn.execute(
                    "UPDATE inventory SET available_quantity = available_quantity + ? WHERE sku = ?",
                    (quantity, sku),
                )
                if restored.rowcount != 1:
                    raise InvalidReservationError(f"Inventory for SKU {sku} not found")

        conn.execute("COMMIT")
        return {
            "reservation_id": reservation_id,
            "sku": sku,
            "quantity": quantity,
            "status": "cancelled",
        }
    except BaseException:
        _rollback(conn)
        raise
    finally:
        conn.close()
