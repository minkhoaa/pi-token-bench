"""Public unit tests for inventory reservation service."""

import os
import tempfile
import unittest

from src.db import init_db, seed_inventory
from src.exceptions import (
    InsufficientStockError,
    InvalidReservationError,
    ReservationNotFoundError,
    SkuNotFoundError,
)
from src.reservations import cancel, get_available, reserve


class TestBasicReservation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        init_db(self.db_path)
        seed_inventory(self.db_path, {"SKU-1": 100, "SKU-2": 20})

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_available(self):
        self.assertEqual(get_available(self.db_path, "SKU-1"), 100)
        self.assertEqual(get_available(self.db_path, "SKU-2"), 20)
        with self.assertRaises(SkuNotFoundError):
            get_available(self.db_path, "NON-EXISTENT")

    def test_reserve_success(self):
        res = reserve(self.db_path, "res-1", "SKU-1", 10)
        self.assertEqual(res["reservation_id"], "res-1")
        self.assertEqual(res["sku"], "SKU-1")
        self.assertEqual(res["quantity"], 10)
        self.assertEqual(res["status"], "active")
        self.assertEqual(get_available(self.db_path, "SKU-1"), 90)

    def test_reserve_insufficient_stock(self):
        with self.assertRaises(InsufficientStockError):
            reserve(self.db_path, "res-too-big", "SKU-2", 25)
        self.assertEqual(get_available(self.db_path, "SKU-2"), 20)

    def test_cancel_restores_stock(self):
        reserve(self.db_path, "res-2", "SKU-1", 15)
        self.assertEqual(get_available(self.db_path, "SKU-1"), 85)

        c = cancel(self.db_path, "res-2")
        self.assertEqual(c["status"], "cancelled")
        self.assertEqual(get_available(self.db_path, "SKU-1"), 100)

    def test_cancel_non_existent(self):
        with self.assertRaises(ReservationNotFoundError):
            cancel(self.db_path, "ghost-res")

    def test_idempotent_reserve(self):
        """Replaying identical reservation ID with identical parameters must be idempotent."""
        res1 = reserve(self.db_path, "idem-1", "SKU-1", 5)
        self.assertEqual(get_available(self.db_path, "SKU-1"), 95)

        # Idempotent retry: must return reservation without deducting extra stock
        res2 = reserve(self.db_path, "idem-1", "SKU-1", 5)
        self.assertEqual(res2["status"], "active")
        self.assertEqual(get_available(self.db_path, "SKU-1"), 95)

    def test_double_cancel_does_not_leak_inventory(self):
        """Repeated cancellation must never release inventory twice."""
        reserve(self.db_path, "dc-1", "SKU-1", 10)
        self.assertEqual(get_available(self.db_path, "SKU-1"), 90)

        cancel(self.db_path, "dc-1")
        self.assertEqual(get_available(self.db_path, "SKU-1"), 100)

        # Second cancel call
        cancel(self.db_path, "dc-1")
        # Stock must remain exactly 100, NOT increment to 110
        self.assertEqual(get_available(self.db_path, "SKU-1"), 100)


if __name__ == "__main__":
    unittest.main()
