"""Inventory reservation service implementation."""

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


def _is_lock_error(error: sqlite3.OperationalError) -> bool:
    code = getattr(error, "sqlite_errorcode", None)
    if code is not None:
        return (code & 0xFF) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    message = str(error).lower()
    return "locked" in message or "busy" in message


def _rollback(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            # Closing the connection below also rolls back any open transaction.
            pass


def _run_write(db_path: str, operation) -> dict:
    """Run an operation in a serialized write transaction, retrying lock conflicts."""
    while True:
        conn = None
        try:
            conn = get_connection(db_path)
            conn.execute("BEGIN IMMEDIATE")
            result = operation(conn)
            conn.execute("COMMIT")
            return result
        except sqlite3.OperationalError as error:
            if conn is not None:
                _rollback(conn)
            if not _is_lock_error(error):
                raise
            time.sleep(0.01)
        except BaseException:
            if conn is not None:
                _rollback(conn)
            raise
        finally:
            if conn is not None:
                conn.close()


def _validate_reservation_id(reservation_id: str) -> None:
    if (
        not isinstance(reservation_id, str)
        or not reservation_id
        or reservation_id != reservation_id.strip()
    ):
        raise InvalidReservationError("reservation_id must be a non-empty, stripped string")


def get_available(db_path: str, sku: str) -> int:
    """Return available inventory quantity for SKU."""
    while True:
        conn = None
        try:
            conn = get_connection(db_path)
            row = conn.execute(
                "SELECT available_quantity FROM inventory WHERE sku = ?", (sku,)
            ).fetchone()
            if row is None:
                raise SkuNotFoundError(f"SKU {sku} not found")
            return int(row["available_quantity"])
        except sqlite3.OperationalError as error:
            if not _is_lock_error(error):
                raise
            time.sleep(0.01)
        finally:
            if conn is not None:
                conn.close()


def reserve(db_path: str, reservation_id: str, sku: str, quantity: int) -> dict:
    """Reserve inventory atomically and idempotently."""
    _validate_reservation_id(reservation_id)
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise InvalidReservationError("quantity must be a positive integer")

    def operation(conn: sqlite3.Connection) -> dict:
        existing = conn.execute(
            "SELECT sku, quantity, status FROM reservations WHERE reservation_id = ?",
            (reservation_id,),
        ).fetchone()
        if existing is not None:
            if existing["sku"] != sku or existing["quantity"] != quantity:
                raise InvalidReservationError(
                    "reservation_id already used with different parameters"
                )
            if existing["status"] != "active":
                raise InvalidReservationError("cancelled reservations cannot be reopened")
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

        cursor = conn.execute(
            "UPDATE inventory SET available_quantity = available_quantity - ? "
            "WHERE sku = ? AND available_quantity >= ?",
            (quantity, sku, quantity),
        )
        if cursor.rowcount != 1:
            raise InsufficientStockError(f"Requested {quantity}, available {available}")

        now_iso = datetime.now(timezone.utc).isoformat()
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

    return _run_write(db_path, operation)


def cancel(db_path: str, reservation_id: str) -> dict:
    """Cancel a reservation and restore its inventory at most once."""
    _validate_reservation_id(reservation_id)

    def operation(conn: sqlite3.Connection) -> dict:
        row = conn.execute(
            "SELECT sku, quantity, status FROM reservations WHERE reservation_id = ?",
            (reservation_id,),
        ).fetchone()
        if row is None:
            raise ReservationNotFoundError(f"Reservation {reservation_id} not found")

        sku = row["sku"]
        quantity = row["quantity"]
        if row["status"] == "cancelled":
            return {
                "reservation_id": reservation_id,
                "sku": sku,
                "quantity": quantity,
                "status": "cancelled",
            }
        if row["status"] != "active":
            raise InvalidReservationError("reservation has an invalid status")

        cursor = conn.execute(
            "UPDATE inventory SET available_quantity = available_quantity + ? WHERE sku = ?",
            (quantity, sku),
        )
        if cursor.rowcount != 1:
            raise SkuNotFoundError(f"SKU {sku} not found")

        now_iso = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            "UPDATE reservations SET status = 'cancelled', updated_at = ? "
            "WHERE reservation_id = ? AND status = 'active'",
            (now_iso, reservation_id),
        )
        if cursor.rowcount != 1:
            raise InvalidReservationError("reservation is no longer active")

        return {
            "reservation_id": reservation_id,
            "sku": sku,
            "quantity": quantity,
            "status": "cancelled",
        }

    return _run_write(db_path, operation)
