import shutil
import tempfile
import unittest
from pathlib import Path

from src.exceptions import (
    ConflictingTransactionError,
    InsufficientFundsError,
    InvalidAmountError,
    JournalCorruptionError,
)
from src.ledger import Ledger


class TestBasicLedger(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="ledger_test_basic_"))
        self.ledger = Ledger(self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_initial_balance_is_zero(self):
        self.assertEqual(self.ledger.balance("alice"), 0)
        self.assertEqual(self.ledger.balance("bob"), 0)

    def test_credit_account(self):
        res = self.ledger.credit("tx-1", "alice", 100)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["account"], "alice")
        self.assertEqual(res["amount"], 100)
        self.assertEqual(res["balance"], 100)
        self.assertEqual(self.ledger.balance("alice"), 100)

    def test_credit_multiple_accounts(self):
        self.ledger.credit("tx-1", "alice", 100)
        self.ledger.credit("tx-2", "bob", 250)
        self.assertEqual(self.ledger.balance("alice"), 100)
        self.assertEqual(self.ledger.balance("bob"), 250)

    def test_credit_invalid_amount_negative(self):
        with self.assertRaises(InvalidAmountError):
            self.ledger.credit("tx-neg", "alice", -50)

    def test_credit_invalid_amount_zero(self):
        with self.assertRaises(InvalidAmountError):
            self.ledger.credit("tx-zero", "alice", 0)

    def test_credit_invalid_amount_types(self):
        with self.assertRaises(InvalidAmountError):
            self.ledger.credit("tx-bool", "alice", True)  # booleans rejected
        with self.assertRaises(InvalidAmountError):
            self.ledger.credit("tx-float", "alice", 50.5)  # floats rejected
        with self.assertRaises(InvalidAmountError):
            self.ledger.credit("tx-str", "alice", "100")  # strings rejected

    def test_replay_restores_balances(self):
        self.ledger.credit("tx-1", "alice", 100)
        self.ledger.credit("tx-2", "bob", 200)

        new_ledger = Ledger(self.test_dir)
        self.assertEqual(new_ledger.balance("alice"), 100)
        self.assertEqual(new_ledger.balance("bob"), 200)


if __name__ == "__main__":
    unittest.main()
