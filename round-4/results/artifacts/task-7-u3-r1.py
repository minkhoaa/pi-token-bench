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
    def _validate_name(value: str, field: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} must be a non-empty string")

    @staticmethod
    def _validate_amount(amount: int) -> None:
        if type(amount) is not int or amount <= 0:
            raise InvalidAmountError("amount must be a strictly positive integer")

    @staticmethod
    def _record_from_line(line: bytes, *, complete: bool) -> Record | None:
        """Decode the final line, distinguishing a torn EOF from completed corruption."""
        try:
            data = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if complete:
                raise JournalCorruptionError("Corrupt final journal record") from exc
            return None

        if not isinstance(data, dict) or "checksum" not in data:
            if complete:
                raise JournalCorruptionError("Final journal record missing checksum or not a dict")
            return None
        if data["checksum"] != compute_record_checksum(data):
            raise JournalCorruptionError("Checksum mismatch in final journal record")

        try:
            return Record(
                index=data["index"],
                command=data["command"],
                tx_id=data["tx_id"],
                account=data["account"],
                amount=data["amount"],
                target=data.get("target"),
                checksum=data["checksum"],
            )
        except (KeyError, TypeError) as exc:
            if complete:
                raise JournalCorruptionError(f"Malformed final journal record: {exc}") from exc
            return None

    def _read_records_locked(self) -> list[Record]:
        # Journal.read_records validates checksums and all non-trailing records.
        records = self.journal.read_records()
        if not self.journal_path.exists():
            return records

        raw = self.journal_path.read_bytes()
        if not raw:
            return records

        if raw.endswith(b"\n"):
            line = raw[:-1].rsplit(b"\n", 1)[-1]
            if line.strip():
                self._record_from_line(line, complete=True)
            return records

        # Journal.read_records intentionally ignores a damaged final line. Trim
        # only an actually incomplete EOF record so later appends remain framed.
        offset = raw.rfind(b"\n") + 1
        line = raw[offset:]
        record = self._record_from_line(line, complete=False)
        if record is None:
            with open(self.journal_path, "r+b") as journal_file:
                journal_file.truncate(offset)
                journal_file.flush()
                os.fsync(journal_file.fileno())
        else:
            # A complete, checksummed record may have lost only its delimiter.
            with open(self.journal_path, "ab") as journal_file:
                journal_file.write(b"\n")
                journal_file.flush()
                os.fsync(journal_file.fileno())
        return records

    def _refresh_locked(self) -> None:
        balances: Dict[str, int] = {}
        transactions: Dict[str, dict] = {}
        records = self._read_records_locked()

        for expected_index, record in enumerate(records):
            if type(record.index) is not int or record.index != expected_index:
                raise JournalCorruptionError(f"Invalid journal index at record {expected_index}")
            if record.command not in ("credit", "transfer"):
                raise JournalCorruptionError(f"Unknown command at record {expected_index}")
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
                balances[record.account] = balances.get(record.account, 0) + record.amount
                result = {
                    "status": "success",
                    "command": "credit",
                    "transaction_id": record.tx_id,
                    "account": record.account,
                    "amount": record.amount,
                    "balance": balances[record.account],
                }
            else:
                target = record.target
                if not isinstance(target, str) or not target or target == record.account:
                    raise JournalCorruptionError(f"Invalid transfer target at record {expected_index}")
                source_balance = balances.get(record.account, 0)
                if source_balance < record.amount:
                    raise JournalCorruptionError(f"Overdraft in journal record {expected_index}")
                balances[record.account] = source_balance - record.amount
                balances[target] = balances.get(target, 0) + record.amount
                result = {
                    "status": "success",
                    "command": "transfer",
                    "transaction_id": record.tx_id,
                    "source": record.account,
                    "target": target,
                    "amount": record.amount,
                    "source_balance": balances[record.account],
                    "target_balance": balances[target],
                }
            transactions[record.tx_id] = result

        self._balances = balances
        self._transactions = transactions
        self._next_index = len(records)

    def replay(self) -> Dict[str, int]:
        """Reconstruct balances from the journal, recovering an incomplete EOF record."""
        with self.journal.lock():
            self._refresh_locked()
            return dict(self._balances)

    def balance(self, account: str) -> int:
        """Return the latest balance for an account, defaulting to zero."""
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
                if (previous.get("command"), previous.get("account"), previous.get("amount")) != (
                    "credit",
                    account,
                    amount,
                ):
                    raise ConflictingTransactionError(
                        f"Transaction ID {transaction_id!r} was used with different parameters"
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
        """Atomically transfer funds between distinct accounts."""
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
                if (
                    previous.get("command"),
                    previous.get("source"),
                    previous.get("target"),
                    previous.get("amount"),
                ) != ("transfer", source, target, amount):
                    raise ConflictingTransactionError(
                        f"Transaction ID {transaction_id!r} was used with different parameters"
                    )
                return dict(previous)

            source_balance = self._balances.get(source, 0)
            if source_balance < amount:
                raise InsufficientFundsError(
                    f"Account {source!r} has insufficient funds for transfer"
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
