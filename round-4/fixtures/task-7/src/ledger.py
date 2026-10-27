"""
Defective starting implementation of Ledger.

Known bugs and issues:
- Does not validate amount types (accepts booleans True/False, floats, negatives, zero).
- Does not check for insufficient funds or allows balances to go negative.
- Idempotency is broken: duplicate transaction_ids are treated as new commands and double-applied.
- Does not use Journal locking (fcntl.flock), risking concurrency races.
- Does not use Journal checksum / recovery contract properly.
- Conflicting transaction IDs are ignored or improperly handled.
- Rejected transactions might mutate in-memory state or write journal.
"""

import json
from pathlib import Path
from typing import Dict, Optional

from .exceptions import (
    ConflictingTransactionError,
    DuplicateTransactionError,
    InsufficientFundsError,
    InvalidAmountError,
    JournalCorruptionError,
)
from .journal import Journal
from .models import Record


class Ledger:
    """
    Event-sourced ledger. Defective initial implementation.
    """

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.journal_path = self.directory / "ledger.journal"
        self.journal = Journal(self.journal_path)
        self._balances: Dict[str, int] = {}
        self._transactions: Dict[str, dict] = {}
        self._next_index = 0
        self.replay()

    def replay(self) -> Dict[str, int]:
        """Replay journal records from disk to reconstruct ledger state."""
        self._balances = {}
        self._transactions = {}
        self._next_index = 0

        # Bug: defective reader crashes or doesn't use journal read_records properly
        if self.journal_path.exists():
            with open(self.journal_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    # Bug: crashes directly on malformed trailing line instead of recovery contract
                    data = json.loads(line)
                    cmd = data["command"]
                    tx_id = data["tx_id"]
                    account = data["account"]
                    amount = data["amount"]
                    target = data.get("target")

                    if cmd == "credit":
                        self._balances[account] = self._balances.get(account, 0) + amount
                        self._transactions[tx_id] = {
                            "status": "success",
                            "command": "credit",
                            "transaction_id": tx_id,
                            "account": account,
                            "amount": amount,
                            "balance": self._balances[account],
                        }
                    elif cmd == "transfer":
                        self._balances[account] = self._balances.get(account, 0) - amount
                        self._balances[target] = self._balances.get(target, 0) + amount
                        self._transactions[tx_id] = {
                            "status": "success",
                            "command": "transfer",
                            "transaction_id": tx_id,
                            "source": account,
                            "target": target,
                            "amount": amount,
                            "source_balance": self._balances[account],
                            "target_balance": self._balances[target],
                        }
                    self._next_index = data.get("index", 0) + 1

        return dict(self._balances)

    def balance(self, account: str) -> int:
        """Return the current balance of an account (default 0)."""
        return self._balances.get(account, 0)

    def credit(self, transaction_id: str, account: str, amount: int) -> dict:
        """Credit an account with an amount."""
        # Defective: no type checks (accepts bool, float, negative)
        # Defective: does not use locking
        # Defective: does not check duplicate/conflicting transaction_id properly

        current = self._balances.get(account, 0)
        new_balance = current + amount
        self._balances[account] = new_balance

        rec = Record(
            index=self._next_index,
            command="credit",
            tx_id=transaction_id,
            account=account,
            amount=amount,
        )
        self._next_index += 1

        # Defective: bypasses journal fsync/locking
        line = json.dumps(rec.payload_dict()) + "\n"
        with open(self.journal_path, "a", encoding="utf-8") as f:
            f.write(line)

        res = {
            "status": "success",
            "command": "credit",
            "transaction_id": transaction_id,
            "account": account,
            "amount": amount,
            "balance": new_balance,
        }
        self._transactions[transaction_id] = res
        return res

    def transfer(self, transaction_id: str, source: str, target: str, amount: int) -> dict:
        """Transfer amount from source account to target account."""
        # Defective: no type checks (accepts bool, float, negative)
        # Defective: no balance check (allows negative balance)
        # Defective: does not use locking

        src_bal = self._balances.get(source, 0) - amount
        tgt_bal = self._balances.get(target, 0) + amount
        self._balances[source] = src_bal
        self._balances[target] = tgt_bal

        rec = Record(
            index=self._next_index,
            command="transfer",
            tx_id=transaction_id,
            account=source,
            amount=amount,
            target=target,
        )
        self._next_index += 1

        # Defective: bypasses journal fsync/locking
        line = json.dumps(rec.payload_dict()) + "\n"
        with open(self.journal_path, "a", encoding="utf-8") as f:
            f.write(line)

        res = {
            "status": "success",
            "command": "transfer",
            "transaction_id": transaction_id,
            "source": source,
            "target": target,
            "amount": amount,
            "source_balance": src_bal,
            "target_balance": tgt_bal,
        }
        self._transactions[transaction_id] = res
        return res
