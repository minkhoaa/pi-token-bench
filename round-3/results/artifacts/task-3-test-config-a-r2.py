"""SQLite-backed inventory reservation service."""

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


def _validate_reservation_id(reservation_id: str) -> None:
    if (
        not isinstance(reservation_id, str)
        or not reservation_id
        or reservation_id != reservation_id.strip()
    ):
        raise InvalidReservationError(
            "reservation_id must be a non-empty string without surrounding whitespace"
        )


def _validate_quantity(quantity: int) -> None:
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise InvalidReservationError("quantity must be a positive integer")


def _is_lock_error(error: sqlite3.OperationalError) -> bool:
    code = getattr(error, "sqlite_errorcode", None)
    if code is not None:
        # Extended SQLite result codes keep BUSY/LOCKED in the low byte.
        return (code & 0xFF) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    message = str(error).lower()
    return "locked" in message or "busy" in message


def _begin_immediate(conn: sqlite3.Connection) -> None:
    """Acquire SQLite's write lock, retrying transient contention safely."""
    deadline = time.monotonic() + 60.0
    while True:
        try:
            conn.execute("BEGIN IMMEDIATE")
            return
        except sqlite3.OperationalError as error:
            if not _is_lock_error(error) or time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def _rollback(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            # Preserve the original failure; closing the connection is the final
            # cleanup if SQLite can no longer roll the transaction back.
            pass


def get_available(db_path: str, sku: str) -> int:
    """Return available inventory quantity for SKU."""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT available_quantity FROM inventory WHERE sku = ?", (sku,)
        ).fetchone()
        if row is None:
            raise SkuNotFoundError(f"SKU {sku} not found")
        return int(row["available_quantity"])
    finally:
        conn.close()


def reserve(db_path: str, reservation_id: str, sku: str, quantity: int) -> dict:
    """Atomically reserve stock, with stable reservation-ID idempotency."""
    _validate_reservation_id(reservation_id)
    _validate_quantity(quantity)

    conn = get_connection(db_path)
    try:
        _begin_immediate(conn)
        try:
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

            available = int(row["available_quantity"])
            if available < quantity:
                raise InsufficientStockError(
                    f"Requested {quantity}, available {available}"
                )

            # The write lock is already held; this conditional update also
            # ensures stock can never be decremented below zero.
            updated = conn.execute(
                """
                UPDATE inventory
                SET available_quantity = available_quantity - ?
                WHERE sku = ? AND available_quantity >= ?
                """,
                (quantity, sku, quantity),
            )
            if updated.rowcount != 1:
                current = conn.execute(
                    "SELECT available_quantity FROM inventory WHERE sku = ?", (sku,)
                ).fetchone()
                if current is None:
                    raise SkuNotFoundError(f"SKU {sku} not found")
                raise InsufficientStockError(
                    f"Requested {quantity}, available {current['available_quantity']}"
                )

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

    conn = get_connection(db_path)
    try:
        _begin_immediate(conn)
        try:
            row = conn.execute(
                "SELECT sku, quantity, status FROM reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise ReservationNotFoundError(
                    f"Reservation {reservation_id} not found"
                )

            sku = row["sku"]
            quantity = row["quantity"]
            if row["status"] == "active":
                restored = conn.execute(
                    "UPDATE inventory SET available_quantity = available_quantity + ? WHERE sku = ?",
                    (quantity, sku),
                )
                if restored.rowcount != 1:
                    raise SkuNotFoundError(f"SKU {sku} not found")

                now_iso = datetime.now(timezone.utc).isoformat()
                changed = conn.execute(
                    """
                    UPDATE reservations
                    SET status = 'cancelled', updated_at = ?
                    WHERE reservation_id = ? AND status = 'active'
                    """,
                    (now_iso, reservation_id),
                )
                if changed.rowcount != 1:
                    raise InvalidReservationError(
                        f"Reservation {reservation_id} could not be cancelled"
                    )

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
