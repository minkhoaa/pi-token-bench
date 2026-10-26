#!/usr/bin/env python3
"""CLI utility to bootstrap and seed the inventory database."""

import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.db import init_db, seed_inventory


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else "inventory.db"
    print(f"Bootstrapping database: {db_path}")
    init_db(db_path)
    sample_stock = {
        "WIDGET-A": 100,
        "WIDGET-B": 50,
        "GADGET-X": 10,
    }
    seed_inventory(db_path, sample_stock)
    print("Database successfully initialized and seeded with sample inventory.")


if __name__ == "__main__":
    main()
