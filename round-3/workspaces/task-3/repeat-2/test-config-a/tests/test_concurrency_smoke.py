"""Concurrency smoke tests for inventory reservation service."""

import concurrent.futures
import os
import tempfile
import unittest

from src.db import init_db, seed_inventory
from src.exceptions import InsufficientStockError
from src.reservations import get_available, reserve


class TestConcurrencySmoke(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "concurrency.db")
        init_db(self.db_path)
        # Total 10 units
        seed_inventory(self.db_path, {"CONC-SKU": 10})

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_concurrent_reservations_no_oversubscription(self):
        """
        20 threads compete to reserve 1 unit each from a pool of 10.
        Exactly 10 must succeed and 10 must raise InsufficientStockError.
        Final available stock must be exactly 0 (never negative).
        """
        num_workers = 20
        successes = 0
        failures = 0
        errors = []

        def worker_task(idx):
            res_id = f"worker-{idx}"
            try:
                reserve(self.db_path, res_id, "CONC-SKU", 1)
                return True, None
            except InsufficientStockError:
                return False, None
            except Exception as e:
                return False, e

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker_task, i) for i in range(num_workers)]
            for fut in concurrent.futures.as_completed(futures):
                ok, err = fut.result()
                if ok:
                    successes += 1
                elif err is None:
                    failures += 1
                else:
                    errors.append(err)

        self.assertEqual(len(errors), 0, f"Unexpected unexpected errors during concurrent reserve: {errors}")
        self.assertEqual(successes, 10, f"Expected exactly 10 successes, got {successes}")
        self.assertEqual(failures, 10, f"Expected exactly 10 failures, got {failures}")
        self.assertEqual(get_available(self.db_path, "CONC-SKU"), 0)


if __name__ == "__main__":
    unittest.main()
