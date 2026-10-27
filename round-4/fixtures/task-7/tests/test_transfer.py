import shutil
import tempfile
import unittest
from pathlib import Path

from src.exceptions import (
    InsufficientFundsError,
    InvalidAmountError,
)
from src.ledger import Ledger


class TestTransferLedger(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="ledger_test_transfer_"))
        self.ledger = Ledger(self.test_dir)
        self.ledger.credit("tx-init", "alice", 500)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_transfer_success(self):
        res = self.ledger.transfer("tx-t1", "alice", "bob", 200)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["source"], "alice")
        self.assertEqual(res["target"], "bob")
        self.assertEqual(res["amount"], 200)
        self.assertEqual(res["source_balance"], 300)
        self.assertEqual(res["target_balance"], 200)
        self.assertEqual(self.ledger.balance("alice"), 300)
        self.assertEqual(self.ledger.balance("bob"), 200)

    def test_insufficient_funds_raises_error(self):
        with self.assertRaises(InsufficientFundsError):
            self.ledger.transfer("tx-overdraft", "alice", "bob", 1000)
        # Verify balances unchanged
        self.assertEqual(self.ledger.balance("alice"), 500)
        self.assertEqual(self.ledger.balance("bob"), 0)

    def test_transfer_zero_or_negative_rejected(self):
        with self.assertRaises(InvalidAmountError):
            self.ledger.transfer("tx-t0", "alice", "bob", 0)
        with self.assertRaises(InvalidAmountError):
            self.ledger.transfer("tx-tneg", "alice", "bob", -100)

    def test_transfer_same_account_rejected(self):
        with self.assertRaises(Exception):
            self.ledger.transfer("tx-same", "alice", "alice", 100)

    def test_transfer_durability_across_reopen(self):
        self.ledger.transfer("tx-t1", "alice", "bob", 150)
        reopened = Ledger(self.test_dir)
        self.assertEqual(reopened.balance("alice"), 350)
        self.assertEqual(reopened.balance("bob"), 150)


if __name__ == "__main__":
    unittest.main()
