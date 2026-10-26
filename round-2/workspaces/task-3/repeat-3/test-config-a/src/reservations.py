"""Inventory reservation service implementation."""

import sqlite3
import time
from datetime import datetime, timezone

from src.exceptions import (
    InsufficientStockError,
    InvalidReservationError,
    ReservationNotFoundError,
    SkuNotFoundError,
)


_SQLITE_TIMEOUT_SECONDS = 30.0
_MAX_LOCK_RETRIES = 10


def _is_lock_error(error: sqlite3.OperationalError) -> bool:
    """Return whether SQLite reported a transient lock/busy condition."""
    error_code = getattr(error, "sqlite_errorcode", None)
    if error_code is not None:
        primary_code = error_code & 0xFF
        if primary_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            return True

    message = str(error).lower()
    return "database is locked" in message or "database is busy" in message


def _connect(db_path: str) -> sqlite3.Connection:
    """Open a WAL connection and close it if connection setup fails."""
    conn = sqlite3.connect(
        db_path,
        timeout=_SQLITE_TIMEOUT_SECONDS,
        isolation_level=None,
    )
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {int(_SQLITE_TIMEOUT_SECONDS * 1000)}")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn
    except BaseException:
        conn.close()
        raise


def _validate_reservation_id(reservation_id: str) -> None:
    if (
        not isinstance(reservation_id, str)
        or not reservation_id.strip()
        or reservation_id != reservation_id.strip()
    ):
        raise InvalidReservationError(
            "reservation_id must be a non-empty, stripped string"
        )


def _validate_quantity(quantity: int) -> None:
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise InvalidReservationError("quantity must be a positive integer")


def _write_transaction(db_path: str, operation) -> dict:
    """Run an operation in a serialized write transaction, retrying lock errors."""
    for attempt in range(_MAX_LOCK_RETRIES + 1):
        conn = None
        try:
            conn = _connect(db_path)
            conn.execute("BEGIN IMMEDIATE")
            result = operation(conn)
            conn.execute("COMMIT")
            return result
        except BaseException as error:
            if conn is not None:
                try:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass

            if (
                isinstance(error, sqlite3.OperationalError)
                and _is_lock_error(error)
                and attempt < _MAX_LOCK_RETRIES
            ):
                time.sleep(min(0.01 * (2**attempt), 0.5))
                continue
            raise
        finally:
            if conn is not None:
                conn.close()


def get_available(db_path: str, sku: str) -> int:
    """Return available inventory quantity for SKU."""
    conn = _connect(db_path)
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
    """Atomically reserve inventory, with stable idempotency by reservation ID."""
    _validate_reservation_id(reservation_id)
    _validate_quantity(quantity)

    def reserve_in_transaction(conn: sqlite3.Connection) -> dict:
        existing = conn.execute(
            """SELECT sku, quantity, status
               FROM reservations WHERE reservation_id = ?""",
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
            return {
                "reservation_id": reservation_id,
                "sku": existing["sku"],
                "quantity": existing["quantity"],
                "status": "active",
            }

        inventory = conn.execute(
            "SELECT available_quantity FROM inventory WHERE sku = ?", (sku,)
        ).fetchone()
        if inventory is None:
            raise SkuNotFoundError(f"SKU {sku} not found")

        available = inventory["available_quantity"]
        if available < quantity:
            raise InsufficientStockError(
                f"Requested {quantity}, available {available}"
            )

        # The conditional update is an additional guard against oversubscription;
        # BEGIN IMMEDIATE serializes this transaction against other writers.
        updated = conn.execute(
            """UPDATE inventory
               SET available_quantity = available_quantity - ?
               WHERE sku = ? AND available_quantity >= ?""",
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
            """INSERT INTO reservations
               (reservation_id, sku, quantity, status, created_at, updated_at)
               VALUES (?, ?, ?, 'active', ?, ?)""",
            (reservation_id, sku, quantity, now_iso, now_iso),
        )
        return {
            "reservation_id": reservation_id,
            "sku": sku,
            "quantity": quantity,
            "status": "active",
        }

    return _write_transaction(db_path, reserve_in_transaction)


def cancel(db_path: str, reservation_id: str) -> dict:
    """Cancel an active reservation and restore its stock exactly once."""
    _validate_reservation_id(reservation_id)

    def cancel_in_transaction(conn: sqlite3.Connection) -> dict:
        reservation = conn.execute(
            """SELECT sku, quantity, status
               FROM reservations WHERE reservation_id = ?""",
            (reservation_id,),
        ).fetchone()
        if reservation is None:
            raise ReservationNotFoundError(
                f"Reservation {reservation_id} not found"
            )

        sku = reservation["sku"]
        quantity = reservation["quantity"]
        status = reservation["status"]
        if status == "active":
            restored = conn.execute(
                """UPDATE inventory
                   SET available_quantity = available_quantity + ?
                   WHERE sku = ?""",
                (quantity, sku),
            )
            if restored.rowcount != 1:
                raise SkuNotFoundError(f"SKU {sku} not found")

            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """UPDATE reservations
                   SET status = 'cancelled', updated_at = ?
                   WHERE reservation_id = ? AND status = 'active'""",
                (now_iso, reservation_id),
            )
        elif status != "cancelled":
            raise InvalidReservationError(
                f"Reservation {reservation_id} has an invalid status"
            )

        return {
            "reservation_id": reservation_id,
            "sku": sku,
            "quantity": quantity,
            "status": "cancelled",
        }

    return _write_transaction(db_path, cancel_in_transaction)
