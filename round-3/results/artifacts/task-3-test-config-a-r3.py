"""Inventory reservation service implementation."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from src.db import get_connection
from src.exceptions import (
    InsufficientStockError,
    InvalidReservationError,
    ReservationNotFoundError,
    SkuNotFoundError,
)


@contextmanager
def _write_transaction(db_path: str):
    """Open a connection and hold SQLite's write lock for the full operation."""
    # A longer busy timeout allows concurrent callers to queue behind the writer
    # rather than leaking transient SQLITE_BUSY errors to the service API.
    conn = get_connection(db_path, timeout=30.0)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    # Preserve the exception that caused the transaction to fail.
                    pass
            raise
    finally:
        conn.close()


def _validate_reservation_id(reservation_id: str) -> None:
    if (
        not isinstance(reservation_id, str)
        or not reservation_id
        or reservation_id != reservation_id.strip()
    ):
        raise InvalidReservationError(
            "reservation_id must be a non-empty string without surrounding whitespace"
        )


def _reservation_result(reservation_id: str, sku: str, quantity: int, status: str) -> dict:
    return {
        "reservation_id": reservation_id,
        "sku": sku,
        "quantity": quantity,
        "status": status,
    }


def get_available(db_path: str, sku: str) -> int:
    """Return available inventory quantity for SKU."""
    conn = get_connection(db_path, timeout=30.0)
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
    """Atomically reserve stock, with active reservations being idempotent."""
    _validate_reservation_id(reservation_id)
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise InvalidReservationError("quantity must be a positive integer")

    with _write_transaction(db_path) as conn:
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
                    f"Cancelled reservation {reservation_id} cannot be reopened"
                )
            result = _reservation_result(reservation_id, sku, quantity, "active")
        else:
            stock = conn.execute(
                "SELECT available_quantity FROM inventory WHERE sku = ?", (sku,)
            ).fetchone()
            if stock is None:
                raise SkuNotFoundError(f"SKU {sku} not found")

            available = int(stock["available_quantity"])
            if available < quantity:
                raise InsufficientStockError(
                    f"Requested {quantity}, available {available}"
                )

            # Keep the stock predicate in the write itself as a final invariant
            # guard, in addition to holding BEGIN IMMEDIATE for this transaction.
            updated = conn.execute(
                """
                UPDATE inventory
                SET available_quantity = available_quantity - ?
                WHERE sku = ? AND available_quantity >= ?
                """,
                (quantity, sku, quantity),
            )
            if updated.rowcount != 1:
                raise InsufficientStockError(
                    f"Requested {quantity}, available inventory changed"
                )

            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO reservations
                    (reservation_id, sku, quantity, status, created_at, updated_at)
                VALUES (?, ?, ?, 'active', ?, ?)
                """,
                (reservation_id, sku, quantity, now_iso, now_iso),
            )
            result = _reservation_result(reservation_id, sku, quantity, "active")

    return result


def cancel(db_path: str, reservation_id: str) -> dict:
    """Cancel an active reservation and restore its stock exactly once."""
    _validate_reservation_id(reservation_id)

    with _write_transaction(db_path) as conn:
        row = conn.execute(
            "SELECT sku, quantity, status FROM reservations WHERE reservation_id = ?",
            (reservation_id,),
        ).fetchone()
        if row is None:
            raise ReservationNotFoundError(f"Reservation {reservation_id} not found")

        sku = row["sku"]
        quantity = int(row["quantity"])
        if row["status"] == "cancelled":
            result = _reservation_result(reservation_id, sku, quantity, "cancelled")
        elif row["status"] == "active":
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

            restored = conn.execute(
                """
                UPDATE inventory
                SET available_quantity = available_quantity + ?
                WHERE sku = ? AND available_quantity + ? <= total_quantity
                """,
                (quantity, sku, quantity),
            )
            if restored.rowcount != 1:
                raise InvalidReservationError(
                    f"Inventory for reservation {reservation_id} cannot be restored"
                )
            result = _reservation_result(reservation_id, sku, quantity, "cancelled")
        else:
            raise InvalidReservationError(
                f"Reservation {reservation_id} has an invalid status"
            )

    return result
