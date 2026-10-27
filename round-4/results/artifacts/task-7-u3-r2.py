"""Crash-safe, event-sourced account ledger."""

import json
import os
from pathlib import Path
from typing import Dict

from .exceptions import (
    ConflictingTransactionError,
    InsufficientFundsError,
    InvalidAmountError,
    JournalCorruptionError,
)
from .journal import Journal, compute_record_checksum
from .models import Record


class Ledger:
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
    def _validate_name(value: str, name: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")

    @staticmethod
    def _validate_amount(amount: int) -> None:
        if type(amount) is not int or amount <= 0:
            raise InvalidAmountError("amount must be a strictly positive integer")

    def _read_records(self, repair_tail: bool = False) -> list[Record]:
        """Read journal, distinguishing a truncated EOF fragment from corruption."""
        records = self.journal.read_records()
        if not self.journal_path.exists():
            return records

        raw = self.journal_path.read_bytes()
        if not raw:
            return records

        nonempty_lines = sum(bool(line.strip()) for line in raw.splitlines())
        if raw.endswith(b"\n"):
            if len(records) != nonempty_lines:
                raise JournalCorruptionError("Invalid complete journal record")
            return records

        boundary = raw.rfind(b"\n") + 1
        complete_count = sum(bool(line.strip()) for line in raw[:boundary].splitlines())
        tail = raw[boundary:]
        if len(records) not in (complete_count, complete_count + 1):
            raise JournalCorruptionError("Invalid journal record before trailing fragment")

        tail_is_record = len(records) == complete_count + 1
        if not tail_is_record:
            # Journal.read_records intentionally ignores a bad final line. A complete,
            # checksummed-looking record with a bad checksum is corruption, not a crash tail.
            try:
                data = json.loads(tail.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                data = None
            required = {"index", "command", "tx_id", "account", "amount", "checksum"}
            if isinstance(data, dict) and required.issubset(data):
                if data["checksum"] != compute_record_checksum(data):
                    raise JournalCorruptionError("Checksum mismatch in trailing record")
                raise JournalCorruptionError("Malformed complete trailing record")

            if repair_tail:
                with open(self.journal_path, "r+b") as journal_file:
                    journal_file.truncate(boundary)
                    journal_file.flush()
                    os.fsync(journal_file.fileno())
        elif repair_tail:
            # A valid final JSON record without its framing newline must be separated
            # from the next append.
            with open(self.journal_path, "ab") as journal_file:
                journal_file.write(b"\n")
                journal_file.flush()
                os.fsync(journal_file.fileno())

        return records

    def _load_state(self, repair_tail: bool = False) -> Dict[str, int]:
        records = self._read_records(repair_tail=repair_tail)
        balances: Dict[str, int] = {}
        transactions: Dict[str, dict] = {}

        for expected_index, record in enumerate(records):
            if (
                type(record.index) is not int
                or record.index != expected_index
                or record.command not in ("credit", "transfer")
                or not isinstance(record.tx_id, str)
                or not record.tx_id
                or not isinstance(record.account, str)
                or not record.account
                or type(record.amount) is not int
                or record.amount <= 0
                or record.tx_id in transactions
            ):
                raise JournalCorruptionError(f"Invalid journal record at index {expected_index}")

            if record.command == "credit":
                if record.target is not None:
                    raise JournalCorruptionError(f"Invalid credit record at index {expected_index}")
                new_balance = balances.get(record.account, 0) + record.amount
                balances[record.account] = new_balance
                transactions[record.tx_id] = {
                    "status": "success",
                    "command": "credit",
                    "transaction_id": record.tx_id,
                    "account": record.account,
                    "amount": record.amount,
                    "balance": new_balance,
                }
            else:
                if (
                    not isinstance(record.target, str)
                    or not record.target
                    or record.target == record.account
                ):
                    raise JournalCorruptionError(f"Invalid transfer record at index {expected_index}")
                source_balance = balances.get(record.account, 0)
                if source_balance < record.amount:
                    raise JournalCorruptionError(f"Overdraft in journal at index {expected_index}")
                source_balance -= record.amount
                target_balance = balances.get(record.target, 0) + record.amount
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

        self._balances = balances
        self._transactions = transactions
        self._next_index = len(records)
        return dict(balances)

    def replay(self) -> Dict[str, int]:
        """Reconstruct balances from the durable journal."""
        with self.journal.lock(exclusive=False):
            return self._load_state()

    def balance(self, account: str) -> int:
        """Return the current balance, including writes from other ledger instances."""
        self._validate_name(account, "account")
        with self.journal.lock(exclusive=False):
            self._load_state()
            return self._balances.get(account, 0)

    def credit(self, transaction_id: str, account: str, amount: int) -> dict:
        self._validate_name(transaction_id, "transaction_id")
        self._validate_name(account, "account")
        self._validate_amount(amount)

        with self.journal.lock():
            self._load_state()
            previous = self._transactions.get(transaction_id)
            if previous is not None:
                if (
                    previous["command"] != "credit"
                    or previous["account"] != account
                    or previous["amount"] != amount
                ):
                    raise ConflictingTransactionError(transaction_id)
                return dict(previous)

            current = self._balances.get(account, 0)
            result = {
                "status": "success",
                "command": "credit",
                "transaction_id": transaction_id,
                "account": account,
                "amount": amount,
                "balance": current + amount,
            }
            self._load_state(repair_tail=True)
            self.journal.append(
                Record(
                    index=self._next_index,
                    command="credit",
                    tx_id=transaction_id,
                    account=account,
                    amount=amount,
                )
            )
            self._balances[account] = result["balance"]
            self._transactions[transaction_id] = result
            self._next_index += 1
            return dict(result)

    def transfer(self, transaction_id: str, source: str, target: str, amount: int) -> dict:
        self._validate_name(transaction_id, "transaction_id")
        self._validate_name(source, "source")
        self._validate_name(target, "target")
        self._validate_amount(amount)
        if source == target:
            raise ValueError("source and target must be different accounts")

        with self.journal.lock():
            self._load_state()
            previous = self._transactions.get(transaction_id)
            if previous is not None:
                if (
                    previous["command"] != "transfer"
                    or previous["source"] != source
                    or previous["target"] != target
                    or previous["amount"] != amount
                ):
                    raise ConflictingTransactionError(transaction_id)
                return dict(previous)

            source_balance = self._balances.get(source, 0)
            if source_balance < amount:
                raise InsufficientFundsError(
                    f"account {source!r} has insufficient funds for {amount}"
                )

            result = {
                "status": "success",
                "command": "transfer",
                "transaction_id": transaction_id,
                "source": source,
                "target": target,
                "amount": amount,
                "source_balance": source_balance - amount,
                "target_balance": self._balances.get(target, 0) + amount,
            }
            self._load_state(repair_tail=True)
            self.journal.append(
                Record(
                    index=self._next_index,
                    command="transfer",
                    tx_id=transaction_id,
                    account=source,
                    amount=amount,
                    target=target,
                )
            )
            self._balances[source] = result["source_balance"]
            self._balances[target] = result["target_balance"]
            self._transactions[transaction_id] = result
            self._next_index += 1
            return dict(result)
