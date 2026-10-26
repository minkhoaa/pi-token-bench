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
        or not reservation_id
        or reservation_id != reservation_id.strip()
    ):
        raise InvalidReservationError("reservation_id must be a non-empty, stripped string")


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
    """Atomically reserve stock; repeated identical active requests are idempotent."""
    _validate_reservation_id(reservation_id)
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise InvalidReservationError("quantity must be a positive integer")

    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
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
                        f"Cancelled reservation {reservation_id} cannot be reopened"
                    )
                conn.commit()
                return {
                    "reservation_id": reservation_id,
                    "sku": existing["sku"],
                    "quantity": existing["quantity"],
                    "status": existing["status"],
                }

            row = conn.execute(
                "SELECT available_quantity FROM inventory WHERE sku = ?", (sku,)
            ).fetchone()
            if row is None:
                raise SkuNotFoundError(f"SKU {sku} not found")

            available = row["available_quantity"]
            if available < quantity:
                raise InsufficientStockError(
                    f"Requested {quantity}, available {available}"
                )

            now_iso = datetime.now(timezone.utc).isoformat()
            updated = conn.execute(
                "UPDATE inventory SET available_quantity = available_quantity - ? "
                "WHERE sku = ? AND available_quantity >= ?",
                (quantity, sku, quantity),
            )
            if updated.rowcount != 1:
                raise InsufficientStockError(
                    f"Requested {quantity}, available {available}"
                )

            conn.execute(
                """
                INSERT INTO reservations (reservation_id, sku, quantity, status, created_at, updated_at)
                VALUES (?, ?, ?, 'active', ?, ?)
                """,
                (reservation_id, sku, quantity, now_iso, now_iso),
            )
            conn.commit()
            return {
                "reservation_id": reservation_id,
                "sku": sku,
                "quantity": quantity,
                "status": "active",
            }
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
    finally:
        conn.close()


def cancel(db_path: str, reservation_id: str) -> dict:
    """Cancel a reservation and restore its stock at most once."""
    _validate_reservation_id(reservation_id)

    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT sku, quantity, status FROM reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise ReservationNotFoundError(
                    f"Reservation {reservation_id} not found"
                )

            result = {
                "reservation_id": reservation_id,
                "sku": row["sku"],
                "quantity": row["quantity"],
                "status": "cancelled",
            }
            if row["status"] == "cancelled":
                conn.commit()
                return result

            now_iso = datetime.now(timezone.utc).isoformat()
            restored = conn.execute(
                "UPDATE inventory SET available_quantity = available_quantity + ? "
                "WHERE sku = ?",
                (row["quantity"], row["sku"]),
            )
            if restored.rowcount != 1:
                raise SkuNotFoundError(f"SKU {row['sku']} not found")

            changed = conn.execute(
                "UPDATE reservations SET status = 'cancelled', updated_at = ? "
                "WHERE reservation_id = ? AND status = 'active'",
                (now_iso, reservation_id),
            )
            if changed.rowcount != 1:
                raise InvalidReservationError(
                    f"Reservation {reservation_id} is not active"
                )

            conn.commit()
            return result
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
    finally:
        conn.close()
