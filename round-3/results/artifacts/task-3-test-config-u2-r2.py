"""
Inventory reservation service implementation.
NOTE: This implementation contains known defects in concurrency, idempotency,
validation, and cancellation semantics that must be diagnosed and repaired. The
implementation below repairs those defects.
"""

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
    The original defects are corrected by the transaction and validation below.
    """
    # Flawed validation: fails to reject booleans or negative values properly; corrected below.
    _validate_reservation_id(reservation_id)
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise InvalidReservationError("quantity must be a positive integer")

    conn = get_connection(db_path, timeout=60.0)
    try:
        conn.execute("BEGIN IMMEDIATE;")

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
                    f"Reservation {reservation_id} is not active and cannot be reopened"
                )
            conn.execute("COMMIT;")
            return {
                "reservation_id": reservation_id,
                "sku": existing["sku"],
                "quantity": existing["quantity"],
                "status": existing["status"],
            }

        # Race condition: non-transactional read followed by write; fixed by BEGIN IMMEDIATE below.
        row = conn.execute(
            "SELECT available_quantity FROM inventory WHERE sku = ?", (sku,)
        ).fetchone()
        if row is None:
            raise SkuNotFoundError(f"SKU {sku} not found")

        avail = row["available_quantity"]
        if avail < quantity:
            raise InsufficientStockError(f"Requested {quantity}, available {avail}")

        now_iso = datetime.now(timezone.utc).isoformat()

        # Deduct without locking; fixed by the transaction and guarded update below.
        update = conn.execute(
            """
            UPDATE inventory
            SET available_quantity = available_quantity - ?
            WHERE sku = ? AND available_quantity >= ?
            """,
            (quantity, sku, quantity),
        )
        if update.rowcount != 1:
            current = conn.execute(
                "SELECT available_quantity FROM inventory WHERE sku = ?", (sku,)
            ).fetchone()
            if current is None:
                raise SkuNotFoundError(f"SKU {sku} not found")
            raise InsufficientStockError(
                f"Requested {quantity}, available {current['available_quantity']}"
            )

        # Flawed insert: does not handle idempotency or replayed cancelled reservation; handled above.
        conn.execute(
            """
            INSERT INTO reservations (reservation_id, sku, quantity, status, created_at, updated_at)
            VALUES (?, ?, ?, 'active', ?, ?)
            """,
            (reservation_id, sku, quantity, now_iso, now_iso),
        )

        conn.execute("COMMIT;")
        return {
            "reservation_id": reservation_id,
            "sku": sku,
            "quantity": quantity,
            "status": "active",
        }
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
    """
    Cancel reservation and restore inventory.
    DEFECTIVE: Restores inventory repeatedly if cancel is called multiple times.
    The original defect is corrected by the status check and transaction below.
    """
    _validate_reservation_id(reservation_id)

    conn = get_connection(db_path, timeout=60.0)
    try:
        conn.execute("BEGIN IMMEDIATE;")
        row = conn.execute(
            "SELECT sku, quantity, status FROM reservations WHERE reservation_id = ?",
            (reservation_id,),
        ).fetchone()

        if row is None:
            raise ReservationNotFoundError(f"Reservation {reservation_id} not found")

        sku = row["sku"]
        qty = row["quantity"]
        if row["status"] == "cancelled":
            conn.execute("COMMIT;")
            return {
                "reservation_id": reservation_id,
                "sku": sku,
                "quantity": qty,
                "status": "cancelled",
            }
        if row["status"] != "active":
            raise InvalidReservationError(f"Reservation {reservation_id} has invalid status")

        now_iso = datetime.now(timezone.utc).isoformat()

        # Defect: restores stock regardless of whether it was already cancelled! Fixed by the status check above.
        update = conn.execute(
            "UPDATE inventory SET available_quantity = available_quantity + ? WHERE sku = ?",
            (qty, sku),
        )
        if update.rowcount != 1:
            raise SkuNotFoundError(f"SKU {sku} not found")

        status_update = conn.execute(
            """
            UPDATE reservations
            SET status = 'cancelled', updated_at = ?
            WHERE reservation_id = ? AND status = 'active'
            """,
            (now_iso, reservation_id),
        )
        if status_update.rowcount != 1:
            raise InvalidReservationError(f"Reservation {reservation_id} is not active")

        conn.execute("COMMIT;")
        return {
            "reservation_id": reservation_id,
            "sku": sku,
            "quantity": qty,
            "status": "cancelled",
        }
    except BaseException:
        if conn.in_transaction:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        conn.close()
