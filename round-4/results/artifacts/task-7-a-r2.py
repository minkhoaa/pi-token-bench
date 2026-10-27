"""Crash-safe, event-sourced financial ledger."""

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
    """Ledger whose durable source of truth is its append-only journal."""

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
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise InvalidAmountError("amount must be a strictly positive integer")

    def _read_records_unlocked(self):
        """Read journal records and repair only an incomplete final frame.

        Journal.read_records() performs checksum validation and rejects corrupt
        middle records. Its recovery contract ignores an invalid final line; here
        we additionally distinguish an unterminated torn write from a complete,
        tampered final record, and remove/finalize the tail so later appends remain
        valid JSON lines.
        """
        records = self.journal.read_records()
        if not self.journal_path.exists():
            return records

        with open(self.journal_path, "rb") as stream:
            raw_lines = stream.readlines()
        nonempty_count = sum(1 for line in raw_lines if line.strip())
        if len(records) != nonempty_count:
            # A damaged record may be discarded only when it is the final,
            # unterminated line. A newline means the record was fully framed.
            if (
                not raw_lines
                or raw_lines[-1].endswith(b"\n")
                or not raw_lines[-1].strip()
                or len(records) != sum(1 for line in raw_lines[:-1] if line.strip())
            ):
                raise JournalCorruptionError("Corrupt or incomplete framed journal record")

            tail = raw_lines[-1]
            parsed = True
            try:
                data = json.loads(tail.decode("utf-8"))
            except Exception:
                data = None
                parsed = False

            # A syntactically complete, checksummed record with a bad checksum
            # is tampering, not a torn write, even if its newline is missing.
            if isinstance(data, dict) and "checksum" in data:
                if data["checksum"] != compute_record_checksum(data):
                    raise JournalCorruptionError("Checksum mismatch in final journal record")
                try:
                    Record(
                        index=data["index"],
                        command=data["command"],
                        tx_id=data["tx_id"],
                        account=data["account"],
                        amount=data["amount"],
                        target=data.get("target"),
                        checksum=data["checksum"],
                    )
                except Exception as exc:
                    raise JournalCorruptionError(
                        f"Malformed final journal record: {exc}"
                    ) from exc
                # A valid record should have been returned by Journal.
                raise JournalCorruptionError("Valid final journal record was not read")
            if parsed and not isinstance(data, dict):
                raise JournalCorruptionError("Final journal record is not an object")

            # Remove the torn record before a future append can concatenate with it.
            valid_size = sum(len(line) for line in raw_lines[:-1])
            with open(self.journal_path, "r+b") as stream:
                stream.truncate(valid_size)
                stream.flush()
                os.fsync(stream.fileno())

        elif raw_lines and not raw_lines[-1].endswith(b"\n"):
            # A crash can occur after the entire checksummed JSON payload but
            # before its line terminator. Preserve that valid record and complete
            # its frame before another append.
            with open(self.journal_path, "ab") as stream:
                stream.write(b"\n")
                stream.flush()
                os.fsync(stream.fileno())

        return records

    def _replay_unlocked(self) -> Dict[str, int]:
        records = self._read_records_unlocked()
        balances: Dict[str, int] = {}
        transactions: Dict[str, dict] = {}

        for expected_index, record in enumerate(records):
            if type(record.index) is not int or record.index != expected_index:
                raise JournalCorruptionError(f"Invalid journal index at record {expected_index}")
            if not isinstance(record.tx_id, str) or not record.tx_id:
                raise JournalCorruptionError(f"Invalid transaction id at record {expected_index}")
            if not isinstance(record.account, str) or not record.account:
                raise JournalCorruptionError(f"Invalid account at record {expected_index}")
            if (
                not isinstance(record.amount, int)
                or isinstance(record.amount, bool)
                or record.amount <= 0
            ):
                raise JournalCorruptionError(f"Invalid amount at record {expected_index}")
            if record.tx_id in transactions:
                raise JournalCorruptionError(f"Duplicate transaction id {record.tx_id!r}")

            if record.command == "credit":
                if record.target is not None:
                    raise JournalCorruptionError("Credit record unexpectedly has a target")
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
            elif record.command == "transfer":
                target = record.target
                if not isinstance(target, str) or not target or target == record.account:
                    raise JournalCorruptionError(f"Invalid transfer target at record {expected_index}")
                source_balance = balances.get(record.account, 0)
                if source_balance < record.amount:
                    raise JournalCorruptionError(
                        f"Transfer overdrafts account {record.account!r} at record {expected_index}"
                    )
                target_balance = balances.get(target, 0)
                source_balance -= record.amount
                target_balance += record.amount
                balances[record.account] = source_balance
                balances[target] = target_balance
                transactions[record.tx_id] = {
                    "status": "success",
                    "command": "transfer",
                    "transaction_id": record.tx_id,
                    "source": record.account,
                    "target": target,
                    "amount": record.amount,
                    "source_balance": source_balance,
                    "target_balance": target_balance,
                }
            else:
                raise JournalCorruptionError(f"Unknown command at record {expected_index}")

        self._balances = balances
        self._transactions = transactions
        self._next_index = len(records)
        return dict(balances)

    def replay(self) -> Dict[str, int]:
        """Reconstruct balances from the journal while coordinating with writers."""
        with self.journal.lock():
            return self._replay_unlocked()

    def balance(self, account: str) -> int:
        """Return an account's current balance, or zero if it is unknown."""
        self._validate_name(account, "account")
        with self.journal.lock():
            self._replay_unlocked()
            return self._balances.get(account, 0)

    @staticmethod
    def _existing_result(
        transactions: Dict[str, dict], transaction_id: str, command: str, **params
    ):
        previous = transactions.get(transaction_id)
        if previous is None:
            return None
        if previous.get("command") != command or any(
            previous.get(key) != value for key, value in params.items()
        ):
            raise ConflictingTransactionError(
                f"Transaction id {transaction_id!r} was already used with different parameters"
            )
        return dict(previous)

    def credit(self, transaction_id: str, account: str, amount: int) -> dict:
        """Credit an account, durably and idempotently."""
        self._validate_name(transaction_id, "transaction_id")
        self._validate_name(account, "account")
        self._validate_amount(amount)

        with self.journal.lock():
            self._replay_unlocked()
            previous = self._existing_result(
                self._transactions,
                transaction_id,
                "credit",
                account=account,
                amount=amount,
            )
            if previous is not None:
                return previous

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

        with self.journal.lock():
            self._replay_unlocked()
            previous = self._existing_result(
                self._transactions,
                transaction_id,
                "transfer",
                source=source,
                target=target,
                amount=amount,
            )
            if previous is not None:
                return previous
            if source == target:
                raise ValueError("source and target accounts must differ")

            source_balance = self._balances.get(source, 0)
            if source_balance < amount:
                raise InsufficientFundsError(
                    f"Account {source!r} has {source_balance}; {amount} required"
                )
            target_balance = self._balances.get(target, 0) + amount
            source_balance -= amount

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
                "source_balance": source_balance,
                "target_balance": target_balance,
            }
            self._balances[source] = source_balance
            self._balances[target] = target_balance
            self._transactions[transaction_id] = result
            self._next_index += 1
            return dict(result)
