"""Atomic, idempotent inventory reservation operations backed by SQLite."""

from datetime import datetime, timezone
from src.db import get_connection
from src.exceptions import (
    InsufficientStockError,
    InvalidReservationError,
    ReservationNotFoundError,
    SkuNotFoundError,
)


def _normalize_reservation_id(reservation_id: str) -> str:
    """Validate and canonicalize a reservation ID."""
    if not isinstance(reservation_id, str):
        raise InvalidReservationError("reservation_id must be a non-empty string")
    normalized = reservation_id.strip()
    if not normalized:
        raise InvalidReservationError("reservation_id must be a non-empty string")
    return normalized


def _rollback_if_needed(conn) -> None:
    """Roll back an unfinished transaction without masking its original error."""
    if conn.in_transaction:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass


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
    """Atomically reserve stock, with reservation-ID idempotency."""
    reservation_id = _normalize_reservation_id(reservation_id)
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise InvalidReservationError("quantity must be a positive integer")

    # A longer busy timeout lets concurrent writers wait for the serialized
    # BEGIN IMMEDIATE transaction rather than surfacing transient SQLITE_BUSY.
    conn = get_connection(db_path, timeout=30.0)
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")

            existing = conn.execute(
                "SELECT sku, quantity, status FROM reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if existing is not None:
                if existing["status"] != "active":
                    raise InvalidReservationError(
                        f"Reservation {reservation_id} has already been cancelled"
                    )
                if existing["sku"] != sku or existing["quantity"] != quantity:
                    raise InvalidReservationError(
                        f"Reservation {reservation_id} already exists with different parameters"
                    )

                result = {
                    "reservation_id": reservation_id,
                    "sku": existing["sku"],
                    "quantity": existing["quantity"],
                    "status": "active",
                }
                conn.execute("COMMIT")
                return result

            inventory = conn.execute(
                "SELECT available_quantity FROM inventory WHERE sku = ?", (sku,)
            ).fetchone()
            if inventory is None:
                raise SkuNotFoundError(f"SKU {sku} not found")

            available = int(inventory["available_quantity"])
            if available < quantity:
                raise InsufficientStockError(f"Requested {quantity}, available {available}")

            # The predicate is an additional safeguard; BEGIN IMMEDIATE already
            # excludes competing writers until both this update and insert commit.
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
            _rollback_if_needed(conn)
            raise
    finally:
        conn.close()


def cancel(db_path: str, reservation_id: str) -> dict:
    """Cancel a reservation once and restore its inventory atomically."""
    reservation_id = _normalize_reservation_id(reservation_id)

    conn = get_connection(db_path, timeout=30.0)
    try:
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
            if row["status"] == "cancelled":
                result = {
                    "reservation_id": reservation_id,
                    "sku": sku,
                    "quantity": quantity,
                    "status": "cancelled",
                }
                conn.execute("COMMIT")
                return result
            if row["status"] != "active":
                raise InvalidReservationError(f"Reservation {reservation_id} has an invalid status")

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
                raise InvalidReservationError(f"Reservation {reservation_id} is not active")

            restored = conn.execute(
                "UPDATE inventory SET available_quantity = available_quantity + ? WHERE sku = ?",
                (quantity, sku),
            )
            if restored.rowcount != 1:
                raise SkuNotFoundError(f"SKU {sku} not found")

            conn.execute("COMMIT")
            return {
                "reservation_id": reservation_id,
                "sku": sku,
                "quantity": quantity,
                "status": "cancelled",
            }
        except BaseException:
            _rollback_if_needed(conn)
            raise
    finally:
        conn.close()
