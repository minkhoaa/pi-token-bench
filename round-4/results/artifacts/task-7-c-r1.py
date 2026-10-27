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
    """Ledger backed by a checksummed append-only journal."""

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
    def _validate_name(value: str, field: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} must be a non-empty string")

    @staticmethod
    def _validate_amount(amount: int) -> None:
        if type(amount) is not int or amount <= 0:
            raise InvalidAmountError("amount must be a strictly positive integer")

    @staticmethod
    def _verify_complete_line(line: bytes) -> None:
        """Reject a complete but malformed/corrupt record, including at EOF."""
        try:
            data = json.loads(line.decode("utf-8").strip())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JournalCorruptionError("Malformed complete journal record") from exc

        if not isinstance(data, dict) or "checksum" not in data:
            raise JournalCorruptionError("Journal record is missing its checksum")
        checksum = data["checksum"]
        try:
            expected = compute_record_checksum(data)
        except (TypeError, ValueError) as exc:
            raise JournalCorruptionError("Malformed journal record") from exc
        if not isinstance(checksum, str) or checksum != expected:
            raise JournalCorruptionError("Journal record checksum mismatch")

        required = {"index", "command", "tx_id", "account", "amount", "checksum"}
        allowed = required | {"target"}
        if not required.issubset(data) or not set(data).issubset(allowed):
            raise JournalCorruptionError("Malformed journal record fields")

    def _prepare_journal_tail_locked(self) -> None:
        """Trim an incomplete EOF fragment and frame any complete unterminated record."""
        if not self.journal_path.exists():
            return

        raw = self.journal_path.read_bytes()
        if not raw:
            return

        if raw.endswith(b"\n"):
            # Journal.read_records treats a bad last line as recoverable. A newline
            # proves that the record was fully framed, so reject checksum tampering.
            for line in reversed(raw.split(b"\n")):
                if line.strip():
                    self._verify_complete_line(line)
                    break
            return

        start = raw.rfind(b"\n") + 1
        tail = raw[start:]
        try:
            json.loads(tail.decode("utf-8").strip())
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Only the final, unterminated fragment is recoverable.
            with open(self.journal_path, "r+b") as journal_file:
                journal_file.truncate(start)
                journal_file.flush()
                os.fsync(journal_file.fileno())
            return

        # Valid JSON without a checksum is not an interrupted Journal.append
        # frame. A full checksum-valid record can be safely terminated instead.
        self._verify_complete_line(tail)
        with open(self.journal_path, "ab") as journal_file:
            journal_file.write(b"\n")
            journal_file.flush()
            os.fsync(journal_file.fileno())

    def _read_records_locked(self) -> list[Record]:
        self._prepare_journal_tail_locked()
        return self.journal.read_records()

    def _rebuild_locked(self) -> None:
        records = self._read_records_locked()
        balances: Dict[str, int] = {}
        transactions: Dict[str, dict] = {}

        for expected_index, record in enumerate(records):
            if type(record.index) is not int or record.index != expected_index:
                raise JournalCorruptionError(
                    f"Unexpected journal index at record {expected_index}"
                )
            if not isinstance(record.tx_id, str) or not record.tx_id:
                raise JournalCorruptionError("Journal transaction ID is invalid")
            if not isinstance(record.account, str) or not record.account:
                raise JournalCorruptionError("Journal account is invalid")
            if type(record.amount) is not int or record.amount <= 0:
                raise JournalCorruptionError("Journal amount is invalid")
            if record.tx_id in transactions:
                raise JournalCorruptionError("Duplicate transaction ID in journal")

            if record.command == "credit":
                if record.target is not None:
                    raise JournalCorruptionError("Credit record has a transfer target")
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
                if not isinstance(record.target, str) or not record.target:
                    raise JournalCorruptionError("Transfer target is invalid")
                if record.account == record.target:
                    raise JournalCorruptionError("Transfer uses the same account twice")
                source_balance = balances.get(record.account, 0)
                if source_balance < record.amount:
                    raise JournalCorruptionError("Journal contains an overdraft")
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
            else:
                raise JournalCorruptionError("Journal command is invalid")

        self._balances = balances
        self._transactions = transactions
        self._next_index = len(records)

    def replay(self) -> Dict[str, int]:
        """Reconstruct balances and transaction results from the journal."""
        with self.journal.lock():
            self._rebuild_locked()
            return dict(self._balances)

    def balance(self, account: str) -> int:
        """Return the latest balance for an account, or zero if it has no history."""
        self._validate_name(account, "account")
        with self.journal.lock():
            self._rebuild_locked()
            return self._balances.get(account, 0)

    def credit(self, transaction_id: str, account: str, amount: int) -> dict:
        """Credit an account once for the given transaction ID."""
        self._validate_name(transaction_id, "transaction_id")
        self._validate_name(account, "account")
        self._validate_amount(amount)

        with self.journal.lock():
            self._rebuild_locked()
            previous = self._transactions.get(transaction_id)
            if previous is not None:
                if (
                    previous["command"] == "credit"
                    and previous["account"] == account
                    and previous["amount"] == amount
                ):
                    return dict(previous)
                raise ConflictingTransactionError(
                    f"Transaction ID {transaction_id!r} was already used"
                )

            balance = self._balances.get(account, 0) + amount
            result = {
                "status": "success",
                "command": "credit",
                "transaction_id": transaction_id,
                "account": account,
                "amount": amount,
                "balance": balance,
            }
            record = Record(
                index=self._next_index,
                command="credit",
                tx_id=transaction_id,
                account=account,
                amount=amount,
            )
            self.journal.append(record)

            self._balances[account] = balance
            self._transactions[transaction_id] = result
            self._next_index += 1
            return dict(result)

    def transfer(
        self, transaction_id: str, source: str, target: str, amount: int
    ) -> dict:
        """Transfer funds atomically, rejecting overdrafts and self-transfers."""
        self._validate_name(transaction_id, "transaction_id")
        self._validate_name(source, "source")
        self._validate_name(target, "target")
        self._validate_amount(amount)
        if source == target:
            raise ValueError("source and target must be different accounts")

        with self.journal.lock():
            self._rebuild_locked()
            previous = self._transactions.get(transaction_id)
            if previous is not None:
                if (
                    previous["command"] == "transfer"
                    and previous["source"] == source
                    and previous["target"] == target
                    and previous["amount"] == amount
                ):
                    return dict(previous)
                raise ConflictingTransactionError(
                    f"Transaction ID {transaction_id!r} was already used"
                )

            source_balance = self._balances.get(source, 0)
            if source_balance < amount:
                raise InsufficientFundsError(
                    f"Account {source!r} has insufficient funds"
                )
            source_balance -= amount
            target_balance = self._balances.get(target, 0) + amount
            result = {
                "status": "success",
                "command": "transfer",
                "transaction_id": transaction_id,
                "source": source,
                "target": target,
                "amount": amount,
                "source_balance": source_balance,
                "target_balance": target_balance,
            }
            record = Record(
                index=self._next_index,
                command="transfer",
                tx_id=transaction_id,
                account=source,
                amount=amount,
                target=target,
            )
            self.journal.append(record)

            self._balances[source] = source_balance
            self._balances[target] = target_balance
            self._transactions[transaction_id] = result
            self._next_index += 1
            return dict(result)
