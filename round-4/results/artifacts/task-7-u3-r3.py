"""Crash-safe event-sourced ledger backed by :mod:`src.journal`."""

import json
import os
from pathlib import Path
from typing import Dict

from .exceptions import (
    ConflictingTransactionError,
    InsufficientFundsError,
    InvalidAmountError,
    JournalCorruptionError,
    LedgerError,
)
from .journal import Journal, compute_record_checksum
from .models import Record


class Ledger:
    """Event-sourced ledger with process-safe mutations and idempotent commands."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.journal_path = self.directory / "ledger.journal"
        self.journal = Journal(self.journal_path)
        self._balances: Dict[str, int] = {}
        self._transactions: Dict[str, dict] = {}
        self._next_index = 0
        self.replay()

    @staticmethod
    def _validate_string(value: str, name: str) -> None:
        if not isinstance(value, str) or not value:
            raise LedgerError(f"{name} must be a non-empty string")

    @staticmethod
    def _validate_amount(amount: int) -> None:
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise InvalidAmountError("amount must be a strictly positive integer")

    @staticmethod
    def _record_from_line(line: bytes) -> Record:
        data = json.loads(line.decode("utf-8").strip())
        if not isinstance(data, dict) or "checksum" not in data:
            raise ValueError("record missing checksum or not a dict")
        if data["checksum"] != compute_record_checksum(data):
            raise ValueError("checksum mismatch")
        return Record(
            index=data["index"],
            command=data["command"],
            tx_id=data["tx_id"],
            account=data["account"],
            amount=data["amount"],
            target=data.get("target"),
            checksum=data["checksum"],
        )

    def _repair_trailing_frame(self) -> None:
        """Drop an unframed torn tail; reject complete bad frames; finish valid frames."""
        if not self.journal_path.exists():
            return
        raw = self.journal_path.read_bytes()
        if not raw:
            return

        terminated = raw.endswith(b"\n")
        if terminated:
            end = len(raw) - 1
            start = raw.rfind(b"\n", 0, end) + 1
            line = raw[start:end]
        else:
            start = raw.rfind(b"\n") + 1
            line = raw[start:]

        if not line.strip():
            if not terminated:
                with self.journal_path.open("ab") as journal_file:
                    journal_file.write(b"\n")
                    journal_file.flush()
                    os.fsync(journal_file.fileno())
            return

        try:
            self._record_from_line(line)
        except Exception as exc:
            if terminated:
                raise JournalCorruptionError(f"Corrupt complete trailing journal record: {exc}") from exc
            # The final frame has no newline, so it is an interrupted write.
            with self.journal_path.open("r+b") as journal_file:
                journal_file.truncate(start)
                journal_file.flush()
                os.fsync(journal_file.fileno())
            return

        if not terminated:
            # A complete checksummed record whose delimiter alone was interrupted.
            with self.journal_path.open("ab") as journal_file:
                journal_file.write(b"\n")
                journal_file.flush()
                os.fsync(journal_file.fileno())

    @staticmethod
    def _state_from_records(records):
        balances: Dict[str, int] = {}
        transactions: Dict[str, dict] = {}

        for expected_index, record in enumerate(records):
            if type(record.index) is not int or record.index != expected_index:
                raise JournalCorruptionError(f"Invalid journal index at record {expected_index}")
            if not isinstance(record.tx_id, str) or not record.tx_id:
                raise JournalCorruptionError(f"Invalid transaction id at record {expected_index}")
            if not isinstance(record.account, str) or not record.account:
                raise JournalCorruptionError(f"Invalid account at record {expected_index}")
            if isinstance(record.amount, bool) or not isinstance(record.amount, int) or record.amount <= 0:
                raise JournalCorruptionError(f"Invalid amount at record {expected_index}")
            if record.tx_id in transactions:
                raise JournalCorruptionError(f"Duplicate transaction id at record {expected_index}")

            if record.command == "credit":
                if record.target is not None:
                    raise JournalCorruptionError(f"Unexpected target at record {expected_index}")
                balance = balances.get(record.account, 0) + record.amount
                balances[record.account] = balance
                transactions[record.tx_id] = {
                    "status": "success",
                    "command": "credit",
                    "transaction_id": record.tx_id,
                    "account": record.account,
                    "amount": record.amount,
                    "balance": balance,
                }
            elif record.command == "transfer":
                if not isinstance(record.target, str) or not record.target or record.target == record.account:
                    raise JournalCorruptionError(f"Invalid transfer target at record {expected_index}")
                source_balance = balances.get(record.account, 0)
                if source_balance < record.amount:
                    raise JournalCorruptionError(f"Overdraft in journal record {expected_index}")
                target_balance = balances.get(record.target, 0) + record.amount
                source_balance -= record.amount
                balances[record.account] = source_balance
                balances[record.target] = target_balance
                transactions[record.tx_id] = {
                    "status": "success",
                    "command": "transfer",
                    "transaction_id": record.tx_id,
                    "source": record.account,
                    "target": record.target,
                    "amount": record.amount,
                    "source_balance": source_balance,
                    "target_balance": target_balance,
                }
            else:
                raise JournalCorruptionError(f"Unknown command at record {expected_index}")

        return balances, transactions, len(records)

    def _load_state_locked(self):
        records = self.journal.read_records()
        self._repair_trailing_frame()
        return self._state_from_records(records)

    def _refresh_locked(self) -> None:
        balances, transactions, next_index = self._load_state_locked()
        self._balances = balances
        self._transactions = transactions
        self._next_index = next_index

    def replay(self) -> Dict[str, int]:
        """Rebuild state from journal while coordinating with writers."""
        # Exclusive lock allows clean recovery of an interrupted final append.
        with self.journal.lock():
            self._refresh_locked()
            return dict(self._balances)

    def balance(self, account: str) -> int:
        """Return the current balance, including updates from other ledger instances."""
        self._validate_string(account, "account")
        with self.journal.lock():
            self._refresh_locked()
            return self._balances.get(account, 0)

    def credit(self, transaction_id: str, account: str, amount: int) -> dict:
        """Credit an account once for the given transaction id."""
        self._validate_string(transaction_id, "transaction_id")
        self._validate_string(account, "account")
        self._validate_amount(amount)

        with self.journal.lock():
            self._refresh_locked()
            previous = self._transactions.get(transaction_id)
            if previous is not None:
                if (
                    previous["command"] == "credit"
                    and previous["account"] == account
                    and previous["amount"] == amount
                ):
                    return dict(previous)
                raise ConflictingTransactionError(f"Conflicting transaction id: {transaction_id}")

            new_balance = self._balances.get(account, 0) + amount
            record = Record(
                index=self._next_index,
                command="credit",
                tx_id=transaction_id,
                account=account,
                amount=amount,
            )
            self.journal.append(record)

            result = {
                "status": "success",
                "command": "credit",
                "transaction_id": transaction_id,
                "account": account,
                "amount": amount,
                "balance": new_balance,
            }
            self._balances[account] = new_balance
            self._transactions[transaction_id] = result
            self._next_index += 1
            return dict(result)

    def transfer(self, transaction_id: str, source: str, target: str, amount: int) -> dict:
        """Atomically transfer funds between distinct accounts."""
        self._validate_string(transaction_id, "transaction_id")
        self._validate_string(source, "source")
        self._validate_string(target, "target")
        self._validate_amount(amount)

        with self.journal.lock():
            self._refresh_locked()
            previous = self._transactions.get(transaction_id)
            if previous is not None:
                if (
                    previous["command"] == "transfer"
                    and previous["source"] == source
                    and previous["target"] == target
                    and previous["amount"] == amount
                ):
                    return dict(previous)
                raise ConflictingTransactionError(f"Conflicting transaction id: {transaction_id}")

            if source == target:
                raise LedgerError("source and target must be different accounts")
            source_balance = self._balances.get(source, 0)
            if source_balance < amount:
                raise InsufficientFundsError(f"Insufficient funds in account {source}")
            new_source_balance = source_balance - amount
            new_target_balance = self._balances.get(target, 0) + amount

            record = Record(
                index=self._next_index,
                command="transfer",
                tx_id=transaction_id,
                account=source,
                amount=amount,
                target=target,
            )
            self.journal.append(record)

            result = {
                "status": "success",
                "command": "transfer",
                "transaction_id": transaction_id,
                "source": source,
                "target": target,
                "amount": amount,
                "source_balance": new_source_balance,
                "target_balance": new_target_balance,
            }
            self._balances[source] = new_source_balance
            self._balances[target] = new_target_balance
            self._transactions[transaction_id] = result
            self._next_index += 1
            return dict(result)
