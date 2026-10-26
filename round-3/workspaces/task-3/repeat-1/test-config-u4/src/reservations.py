"""Inventory reservation service implementation."""

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
        or not reservation_id.strip()
        or reservation_id != reservation_id.strip()
    ):
        raise InvalidReservationError("reservation_id must be a non-empty, stripped string")


def _result(reservation_id: str, sku: str, quantity: int, status: str) -> dict:
    return {
        "reservation_id": reservation_id,
        "sku": sku,
        "quantity": quantity,
        "status": status,
    }


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
    """Atomically reserve stock, with replay-safe reservation IDs."""
    _validate_reservation_id(reservation_id)
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise InvalidReservationError("quantity must be a positive integer")

    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT sku, quantity, status FROM reservations WHERE reservation_id = ?",
            (reservation_id,),
        ).fetchone()
        if existing is not None:
            if (
                existing["status"] != "active"
                or existing["sku"] != sku
                or existing["quantity"] != quantity
            ):
                raise InvalidReservationError(
                    f"Reservation {reservation_id} already exists with different or cancelled state"
                )
            result = _result(reservation_id, existing["sku"], existing["quantity"], "active")
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

        changed = conn.execute(
            "UPDATE inventory SET available_quantity = available_quantity - ? "
            "WHERE sku = ? AND available_quantity >= ?",
            (quantity, sku, quantity),
        ).rowcount
        if changed != 1:
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
        return _result(reservation_id, sku, quantity, "active")
    except BaseException:
        if conn.in_transaction:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        conn.close()


def cancel(db_path: str, reservation_id: str) -> dict:
    """Cancel an active reservation once and restore its stock."""
    _validate_reservation_id(reservation_id)

    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT sku, quantity, status FROM reservations WHERE reservation_id = ?",
            (reservation_id,),
        ).fetchone()
        if row is None:
            raise ReservationNotFoundError(f"Reservation {reservation_id} not found")

        sku, quantity, status = row["sku"], row["quantity"], row["status"]
        if status == "cancelled":
            conn.execute("COMMIT")
            return _result(reservation_id, sku, quantity, "cancelled")
        if status != "active":
            raise InvalidReservationError(f"Reservation {reservation_id} has invalid status")

        now_iso = datetime.now(timezone.utc).isoformat()
        changed = conn.execute(
            "UPDATE reservations SET status = 'cancelled', updated_at = ? "
            "WHERE reservation_id = ? AND status = 'active'",
            (now_iso, reservation_id),
        ).rowcount
        if changed != 1:
            raise InvalidReservationError(f"Reservation {reservation_id} is not active")
        restored = conn.execute(
            "UPDATE inventory SET available_quantity = available_quantity + ? WHERE sku = ?",
            (quantity, sku),
        ).rowcount
        if restored != 1:
            raise SkuNotFoundError(f"SKU {sku} not found")

        conn.execute("COMMIT")
        return _result(reservation_id, sku, quantity, "cancelled")
    except BaseException:
        if conn.in_transaction:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        conn.close()
