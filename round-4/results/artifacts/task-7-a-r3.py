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
    """Event-sourced ledger whose journal is the source of truth."""

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
        # bool is a subclass of int, so require the exact integer type.
        if type(amount) is not int or amount <= 0:
            raise InvalidAmountError("amount must be a strictly positive integer")

    def _truncate_locked(self, offset: int) -> None:
        """Truncate an incomplete final frame while the journal lock is held."""
        with open(self.journal_path, "r+b") as journal_file:
            journal_file.truncate(offset)
            journal_file.flush()
            os.fsync(journal_file.fileno())

    def _read_records_locked(self):
        """Validate framing, repair an incomplete tail, then use Journal's reader.

        Journal.read_records() intentionally tolerates a bad last line. Checksum
        mismatches on complete, newline-terminated records must not be mistaken
        for interrupted writes, so verify those frames before calling it.
        """
        if not self.journal_path.exists():
            return []

        raw = self.journal_path.read_bytes()
        ends_with_newline = raw.endswith(b"\n")
        segments = raw.split(b"\n")
        complete_lines = segments[:-1]
        complete_length = len(raw) if ends_with_newline else raw.rfind(b"\n") + 1
        repair_at = None if ends_with_newline or not raw else complete_length

        # A final unterminated fragment is never a complete journal frame.
        # Validate every preceding complete line before trimming it.
        nonblank_indices = [i for i, line in enumerate(complete_lines) if line.strip()]
        last_nonblank = nonblank_indices[-1] if nonblank_indices else None
        line_starts = []
        position = 0
        for line in complete_lines:
            line_starts.append(position)
            position += len(line) + 1

        valid_frame_count = 0
        for i, raw_line in enumerate(complete_lines):
            line = raw_line.strip()
            if not line:
                continue
            is_last_record = i == last_nonblank
            try:
                data = json.loads(line.decode("utf-8"))
            except Exception as exc:
                if is_last_record:
                    repair_at = line_starts[i]
                    break
                raise JournalCorruptionError(f"Corrupt JSON line at record {i}") from exc

            if not isinstance(data, dict) or "checksum" not in data:
                if is_last_record:
                    repair_at = line_starts[i]
                    break
                raise JournalCorruptionError(f"Record {i} missing checksum or not a dict")

            try:
                checksum_matches = data["checksum"] == compute_record_checksum(data)
            except Exception as exc:
                raise JournalCorruptionError(f"Malformed record at record {i}") from exc
            if not checksum_matches:
                raise JournalCorruptionError(f"Checksum mismatch at record {i}")

            command = data.get("command")
            base_keys = {"index", "command", "tx_id", "account", "amount", "checksum"}
            if command == "transfer":
                expected_keys = base_keys | {"target"}
            elif command == "credit":
                expected_keys = base_keys
            else:
                raise JournalCorruptionError(f"Unknown command at record {i}")
            if set(data) != expected_keys:
                raise JournalCorruptionError(f"Malformed record fields at record {i}")
            valid_frame_count += 1

        if repair_at is not None:
            self._truncate_locked(repair_at)

        # This remains the canonical Record decoder and checksum validator.
        records = self.journal.read_records()
        if len(records) != valid_frame_count:
            raise JournalCorruptionError("Journal contains a malformed record")
        return records

    @staticmethod
    def _state_from_records(records):
        balances: Dict[str, int] = {}
        transactions: Dict[str, dict] = {}

        for expected_index, record in enumerate(records):
            if type(record.index) is not int or record.index != expected_index:
                raise JournalCorruptionError(f"Invalid journal index at record {expected_index}")
            if not isinstance(record.tx_id, str) or not record.tx_id:
                raise JournalCorruptionError(f"Invalid transaction ID at record {expected_index}")
            if not isinstance(record.account, str) or not record.account:
                raise JournalCorruptionError(f"Invalid account at record {expected_index}")
            if type(record.amount) is not int or record.amount <= 0:
                raise JournalCorruptionError(f"Invalid amount at record {expected_index}")
            if record.tx_id in transactions:
                raise JournalCorruptionError(f"Duplicate transaction ID at record {expected_index}")

            if record.command == "credit":
                if record.target is not None:
                    raise JournalCorruptionError(f"Unexpected transfer target at record {expected_index}")
                new_balance = balances.get(record.account, 0) + record.amount
                result = {
                    "status": "success",
                    "command": "credit",
                    "transaction_id": record.tx_id,
                    "account": record.account,
                    "amount": record.amount,
                    "balance": new_balance,
                }
                balances[record.account] = new_balance
            elif record.command == "transfer":
                if not isinstance(record.target, str) or not record.target:
                    raise JournalCorruptionError(f"Invalid transfer target at record {expected_index}")
                if record.account == record.target:
                    raise JournalCorruptionError(f"Self-transfer at record {expected_index}")
                source_balance = balances.get(record.account, 0)
                if source_balance < record.amount:
                    raise JournalCorruptionError(f"Overdraft in journal record {expected_index}")
                target_balance = balances.get(record.target, 0) + record.amount
                source_balance -= record.amount
                result = {
                    "status": "success",
                    "command": "transfer",
                    "transaction_id": record.tx_id,
                    "source": record.account,
                    "target": record.target,
                    "amount": record.amount,
                    "source_balance": source_balance,
                    "target_balance": target_balance,
                }
                balances[record.account] = source_balance
                balances[record.target] = target_balance
            else:
                raise JournalCorruptionError(f"Unknown command at record {expected_index}")

            transactions[record.tx_id] = result

        return balances, transactions, len(records)

    def _refresh_locked(self) -> None:
        records = self._read_records_locked()
        balances, transactions, next_index = self._state_from_records(records)
        self._balances = balances
        self._transactions = transactions
        self._next_index = next_index

    def replay(self) -> Dict[str, int]:
        """Reconstruct balances from the journal, recovering a truncated tail."""
        with self.journal.lock():
            self._refresh_locked()
            return dict(self._balances)

    def balance(self, account: str) -> int:
        """Return an account's current balance, or zero if it has no history."""
        self._validate_name(account, "account")
        with self.journal.lock():
            self._refresh_locked()
            return self._balances.get(account, 0)

    def credit(self, transaction_id: str, account: str, amount: int) -> dict:
        """Credit an account once for the given transaction ID."""
        self._validate_name(transaction_id, "transaction_id")
        self._validate_name(account, "account")
        self._validate_amount(amount)

        with self.journal.lock():
            self._refresh_locked()
            previous = self._transactions.get(transaction_id)
            if previous is not None:
                if (
                    previous["command"] != "credit"
                    or previous.get("account") != account
                    or previous["amount"] != amount
                ):
                    raise ConflictingTransactionError(
                        f"Transaction ID {transaction_id!r} was already used with different parameters"
                    )
                return dict(previous)

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
        """Atomically transfer funds between two distinct accounts."""
        self._validate_name(transaction_id, "transaction_id")
        self._validate_name(source, "source")
        self._validate_name(target, "target")
        self._validate_amount(amount)
        if source == target:
            raise ValueError("source and target must be different accounts")

        with self.journal.lock():
            self._refresh_locked()
            previous = self._transactions.get(transaction_id)
            if previous is not None:
                if previous["command"] != "transfer" or (
                    previous["source"],
                    previous["target"],
                    previous["amount"],
                ) != (source, target, amount):
                    raise ConflictingTransactionError(
                        f"Transaction ID {transaction_id!r} was already used with different parameters"
                    )
                return dict(previous)

            source_balance = self._balances.get(source, 0)
            if source_balance < amount:
                raise InsufficientFundsError(
                    f"Account {source!r} has insufficient funds for this transfer"
                )
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
