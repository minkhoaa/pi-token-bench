"""Crash-safe event-sourced ledger backed by :mod:`src.journal`."""

import os
from pathlib import Path
from typing import Dict

from .exceptions import (
    ConflictingTransactionError,
    InsufficientFundsError,
    InvalidAmountError,
    JournalCorruptionError,
)
from .journal import Journal
from .models import Record


class Ledger:
    """A process-safe ledger whose durable state is reconstructed from its journal."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.journal_path = self.directory / "ledger.journal"
        self.journal = Journal(self.journal_path)
        self._balances: Dict[str, int] = {}
        self._transactions: Dict[str, dict] = {}
        self._transaction_parameters: Dict[str, tuple] = {}
        self._next_index = 0
        self.replay()

    @staticmethod
    def _validate_non_empty_string(value: str, name: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")

    @staticmethod
    def _validate_amount(amount: int) -> None:
        # bool is a subclass of int, so an isinstance check is not sufficient.
        if type(amount) is not int or amount <= 0:
            raise InvalidAmountError("amount must be a strictly positive integer")

    @staticmethod
    def _parameters_match(stored: tuple, requested: tuple) -> bool:
        """Compare a request to a persisted signature without bool/int aliasing."""
        if len(stored) != len(requested):
            return False
        for previous, current in zip(stored, requested):
            if isinstance(previous, int):
                if type(current) is not int or previous != current:
                    return False
            elif not isinstance(current, str) or previous != current:
                return False
        return True

    def _read_records_locked(self):
        """Read complete, checksummed records while the journal lock is held.

        ``Journal.read_records`` intentionally tolerates an invalid final line as
        a possible interrupted write. Count newline-terminated records as well so
        a malformed *complete* final line (for example, a tampered checksum) is
        not mistaken for an interrupted write. A final line without its framing
        newline is excluded even if its JSON happens to parse successfully.
        """
        raw = self.journal_path.read_bytes() if self.journal_path.exists() else b""
        records = self.journal.read_records()
        lines = raw.splitlines(keepends=True)
        complete_count = sum(
            1 for line in lines if line.endswith(b"\n") and line.strip()
        )

        if len(records) < complete_count:
            raise JournalCorruptionError(
                "A complete journal record is malformed or has an invalid checksum"
            )
        # A syntactically valid final JSON object without a newline is still an
        # incomplete journal frame and must not be replayed.
        return records[:complete_count], bool(raw and not raw.endswith(b"\n"))

    @staticmethod
    def _rebuild(records):
        """Validate and apply records into fresh state, without partial mutation."""
        balances: Dict[str, int] = {}
        transactions: Dict[str, dict] = {}
        parameters: Dict[str, tuple] = {}

        for expected_index, record in enumerate(records):
            if type(record.index) is not int or record.index != expected_index:
                raise JournalCorruptionError(
                    f"Invalid journal index at record {expected_index}"
                )
            if not isinstance(record.tx_id, str) or not record.tx_id:
                raise JournalCorruptionError(
                    f"Invalid transaction id at record {expected_index}"
                )
            if not isinstance(record.account, str) or not record.account:
                raise JournalCorruptionError(
                    f"Invalid account at record {expected_index}"
                )
            if type(record.amount) is not int or record.amount <= 0:
                raise JournalCorruptionError(
                    f"Invalid amount at record {expected_index}"
                )
            if record.tx_id in transactions:
                raise JournalCorruptionError(
                    f"Duplicate transaction id at record {expected_index}"
                )

            if record.command == "credit":
                if record.target is not None:
                    raise JournalCorruptionError(
                        f"Unexpected transfer target at record {expected_index}"
                    )
                balance = balances.get(record.account, 0) + record.amount
                balances[record.account] = balance
                result = {
                    "status": "success",
                    "command": "credit",
                    "transaction_id": record.tx_id,
                    "account": record.account,
                    "amount": record.amount,
                    "balance": balance,
                }
                signature = ("credit", record.account, record.amount)
            elif record.command == "transfer":
                target = record.target
                if not isinstance(target, str) or not target or target == record.account:
                    raise JournalCorruptionError(
                        f"Invalid transfer target at record {expected_index}"
                    )
                source_balance = balances.get(record.account, 0)
                if source_balance < record.amount:
                    raise JournalCorruptionError(
                        f"Overdraft in journal at record {expected_index}"
                    )
                target_balance = balances.get(target, 0) + record.amount
                source_balance -= record.amount
                balances[record.account] = source_balance
                balances[target] = target_balance
                result = {
                    "status": "success",
                    "command": "transfer",
                    "transaction_id": record.tx_id,
                    "source": record.account,
                    "target": target,
                    "amount": record.amount,
                    "source_balance": source_balance,
                    "target_balance": target_balance,
                }
                signature = ("transfer", record.account, target, record.amount)
            else:
                raise JournalCorruptionError(
                    f"Unknown command at record {expected_index}"
                )

            transactions[record.tx_id] = result
            parameters[record.tx_id] = signature

        return balances, transactions, parameters

    def _truncate_incomplete_tail_locked(self) -> None:
        """Remove an unframed EOF fragment before a subsequent append."""
        if not self.journal_path.exists():
            return
        with self.journal_path.open("r+b") as journal_file:
            raw = journal_file.read()
            if not raw or raw.endswith(b"\n"):
                return
            last_newline = raw.rfind(b"\n")
            journal_file.seek(last_newline + 1)
            journal_file.truncate()
            journal_file.flush()
            os.fsync(journal_file.fileno())

    def _refresh_from_journal_locked(self, repair_tail: bool = False) -> None:
        records, has_incomplete_tail = self._read_records_locked()
        balances, transactions, parameters = self._rebuild(records)
        if repair_tail and has_incomplete_tail:
            self._truncate_incomplete_tail_locked()
        self._balances = balances
        self._transactions = transactions
        self._transaction_parameters = parameters
        self._next_index = len(records)

    def replay(self) -> Dict[str, int]:
        """Reconstruct balances and transaction results from the durable journal."""
        # Recovery may truncate a partial EOF frame, so replay takes the exclusive
        # lock even though its normal operation is a read.
        with self.journal.lock():
            self._refresh_from_journal_locked(repair_tail=True)
            return dict(self._balances)

    def balance(self, account: str) -> int:
        """Return an account's current balance (zero for a new account)."""
        self._validate_non_empty_string(account, "account")
        with self.journal.lock(exclusive=False):
            self._refresh_from_journal_locked()
            return self._balances.get(account, 0)

    def credit(self, transaction_id: str, account: str, amount: int) -> dict:
        """Credit an account, durably appending the event exactly once."""
        self._validate_non_empty_string(transaction_id, "transaction_id")
        requested = ("credit", account, amount)

        with self.journal.lock():
            self._refresh_from_journal_locked()
            previous = self._transactions.get(transaction_id)
            if previous is not None:
                if self._parameters_match(
                    self._transaction_parameters[transaction_id], requested
                ):
                    return dict(previous)
                raise ConflictingTransactionError(
                    f"Transaction id {transaction_id!r} was already used"
                )

            self._validate_non_empty_string(account, "account")
            self._validate_amount(amount)
            new_balance = self._balances.get(account, 0) + amount
            record = Record(
                index=self._next_index,
                command="credit",
                tx_id=transaction_id,
                account=account,
                amount=amount,
            )

            self._truncate_incomplete_tail_locked()
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
            self._transaction_parameters[transaction_id] = requested
            self._next_index += 1
            return dict(result)

    def transfer(
        self, transaction_id: str, source: str, target: str, amount: int
    ) -> dict:
        """Atomically transfer funds, rejecting overdrafts and self-transfers."""
        self._validate_non_empty_string(transaction_id, "transaction_id")
        requested = ("transfer", source, target, amount)

        with self.journal.lock():
            self._refresh_from_journal_locked()
            previous = self._transactions.get(transaction_id)
            if previous is not None:
                if self._parameters_match(
                    self._transaction_parameters[transaction_id], requested
                ):
                    return dict(previous)
                raise ConflictingTransactionError(
                    f"Transaction id {transaction_id!r} was already used"
                )

            self._validate_non_empty_string(source, "source")
            self._validate_non_empty_string(target, "target")
            self._validate_amount(amount)
            if source == target:
                raise ValueError("source and target accounts must be different")

            source_balance = self._balances.get(source, 0)
            if source_balance < amount:
                raise InsufficientFundsError(
                    f"Account {source!r} has insufficient funds"
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

            self._truncate_incomplete_tail_locked()
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
            self._transaction_parameters[transaction_id] = requested
            self._next_index += 1
            return dict(result)
