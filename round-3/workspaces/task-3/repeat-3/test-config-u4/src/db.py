"""Database bootstrap and connection utilities for inventory reservation."""

import sqlite3
from pathlib import Path


def get_connection(db_path: str, timeout: float = 10.0) -> sqlite3.Connection:
    """Open an SQLite connection configured with WAL and busy timeout."""
    conn = sqlite3.connect(db_path, timeout=timeout, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)};")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: str) -> None:
    """Initialize SQLite database with schema."""
    schema_path = Path(__file__).resolve().parent.parent / "schema.sql"
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    conn = get_connection(db_path)
    try:
        conn.executescript(schema_sql)
    finally:
        conn.close()


def seed_inventory(db_path: str, items: dict[str, int]) -> None:
    """Seed initial inventory into database."""
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE;")
        for sku, qty in items.items():
            conn.execute(
                """
                INSERT INTO inventory (sku, total_quantity, available_quantity)
                VALUES (?, ?, ?)
                ON CONFLICT(sku) DO UPDATE SET
                    total_quantity = excluded.total_quantity,
                    available_quantity = excluded.available_quantity;
                """,
                (sku, qty, qty),
            )
        conn.execute("COMMIT;")
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    finally:
        conn.close()
