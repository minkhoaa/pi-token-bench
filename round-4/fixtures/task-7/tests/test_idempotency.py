import shutil
import tempfile
import unittest
from pathlib import Path

from src.exceptions import (
    ConflictingTransactionError,
    InvalidAmountError,
)
from src.ledger import Ledger


class TestIdempotencyLedger(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="ledger_test_idemp_"))
        self.ledger = Ledger(self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_credit_idempotency(self):
        res1 = self.ledger.credit("tx-c1", "alice", 100)
        res2 = self.ledger.credit("tx-c1", "alice", 100)
        self.assertEqual(res1, res2)
        # Should not double credit
        self.assertEqual(self.ledger.balance("alice"), 100)

    def test_credit_conflict_different_amount(self):
        self.ledger.credit("tx-c1", "alice", 100)
        with self.assertRaises(ConflictingTransactionError):
            self.ledger.credit("tx-c1", "alice", 200)
        self.assertEqual(self.ledger.balance("alice"), 100)

    def test_credit_conflict_different_account(self):
        self.ledger.credit("tx-c1", "alice", 100)
        with self.assertRaises(ConflictingTransactionError):
            self.ledger.credit("tx-c1", "bob", 100)
        self.assertEqual(self.ledger.balance("alice"), 100)
        self.assertEqual(self.ledger.balance("bob"), 0)

    def test_transfer_idempotency(self):
        self.ledger.credit("tx-init", "alice", 300)
        res1 = self.ledger.transfer("tx-t1", "alice", "bob", 100)
        res2 = self.ledger.transfer("tx-t1", "alice", "bob", 100)
        self.assertEqual(res1, res2)
        # Balance should be deducted only once
        self.assertEqual(self.ledger.balance("alice"), 200)
        self.assertEqual(self.ledger.balance("bob"), 100)

    def test_transfer_conflict_different_target(self):
        self.ledger.credit("tx-init", "alice", 300)
        self.ledger.transfer("tx-t1", "alice", "bob", 100)
        with self.assertRaises(ConflictingTransactionError):
            self.ledger.transfer("tx-t1", "alice", "charlie", 100)
        self.assertEqual(self.ledger.balance("bob"), 100)
        self.assertEqual(self.ledger.balance("charlie"), 0)

    def test_conflict_credit_vs_transfer(self):
        self.ledger.credit("tx-common", "alice", 200)
        with self.assertRaises(ConflictingTransactionError):
            self.ledger.transfer("tx-common", "alice", "bob", 50)


if __name__ == "__main__":
    unittest.main()
