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
    """A journal-backed ledger whose mutations are serialized by the journal lock."""

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
    def _validate_string(name: str, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")

    @staticmethod
    def _validate_amount(amount: int) -> None:
        if type(amount) is not int or amount <= 0:
            raise InvalidAmountError("amount must be a strictly positive integer")

    def _read_records_locked(self):
        """Validate complete frames and remove an interrupted, unterminated tail."""
        if self.journal_path.exists():
            raw = self.journal_path.read_bytes()
            complete_end = raw.rfind(b"\n") + 1
            complete = raw[:complete_end]
            trailing = raw[complete_end:]

            # Journal.read_records intentionally tolerates a bad final line as a
            # possible interrupted write. A newline-terminated frame is complete,
            # so verify its checksum here, including when it is the final frame.
            for index, line in enumerate(complete.split(b"\n")[:-1]):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line.decode("utf-8"))
                    required = {"index", "command", "tx_id", "account", "amount", "checksum"}
                    if not isinstance(data, dict) or not required.issubset(data):
                        raise ValueError("missing required fields or record is not an object")
                    checksum = data["checksum"]
                    if checksum != compute_record_checksum(data):
                        raise ValueError("checksum mismatch")
                except Exception as exc:
                    raise JournalCorruptionError(
                        f"Corrupt complete journal record {index}: {exc}"
                    ) from exc

            if trailing:
                # A syntactically complete record with a bad checksum is tampering,
                # not a truncated append. Other unterminated tails are recoverable.
                try:
                    data = json.loads(trailing.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    data = None
                if isinstance(data, dict) and "checksum" in data:
                    try:
                        checksum_valid = data["checksum"] == compute_record_checksum(data)
                    except Exception as exc:
                        raise JournalCorruptionError(
                            f"Corrupt trailing journal record: {exc}"
                        ) from exc
                    if not checksum_valid:
                        raise JournalCorruptionError("Checksum mismatch in trailing record")

                # The Journal reader skips this tail but does not physically remove
                # it. Truncate under the lock so the next append starts a new frame.
                with open(self.journal_path, "r+b") as journal_file:
                    journal_file.truncate(complete_end)
                    journal_file.flush()
                    os.fsync(journal_file.fileno())

        return self.journal.read_records()

    def _rebuild(self, records) -> tuple[Dict[str, int], Dict[str, dict], int]:
        balances: Dict[str, int] = {}
        transactions: Dict[str, dict] = {}

        for expected_index, record in enumerate(records):
            if type(record.index) is not int or record.index != expected_index:
                raise JournalCorruptionError(
                    f"Unexpected journal index {record.index!r}; expected {expected_index}"
                )
            if record.command not in ("credit", "transfer"):
                raise JournalCorruptionError(f"Unknown journal command {record.command!r}")
            if not isinstance(record.tx_id, str) or not record.tx_id:
                raise JournalCorruptionError("Invalid transaction ID in journal")
            if not isinstance(record.account, str) or not record.account:
                raise JournalCorruptionError("Invalid account in journal")
            if type(record.amount) is not int or record.amount <= 0:
                raise JournalCorruptionError("Invalid amount in journal")
            if record.tx_id in transactions:
                raise JournalCorruptionError(
                    f"Duplicate transaction ID {record.tx_id!r} in journal"
                )

            if record.command == "credit":
                if record.target is not None:
                    raise JournalCorruptionError("Credit record cannot have a target")
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
                if not isinstance(target, str) or not target:
                    raise JournalCorruptionError("Invalid transfer target in journal")
                if target == record.account:
                    raise JournalCorruptionError("Transfer source and target are identical")
                source_balance = balances.get(record.account, 0)
                if source_balance < record.amount:
                    raise JournalCorruptionError("Journal transfer overdraws its source")
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

        return balances, transactions, len(records)

    def _reload_locked(self) -> None:
        records = self._read_records_locked()
        balances, transactions, next_index = self._rebuild(records)
        self._balances = balances
        self._transactions = transactions
        self._next_index = next_index

    def replay(self) -> Dict[str, int]:
        """Reconstruct state under the journal lock, recovering a truncated tail."""
        with self.journal.lock():
            self._reload_locked()
            return dict(self._balances)

    def balance(self, account: str) -> int:
        """Return the latest persisted balance for an account (default 0)."""
        self._validate_string("account", account)
        with self.journal.lock():
            self._reload_locked()
            return self._balances.get(account, 0)

    def credit(self, transaction_id: str, account: str, amount: int) -> dict:
        """Credit an account once, persisting the event before updating local state."""
        self._validate_string("transaction_id", transaction_id)
        self._validate_string("account", account)
        self._validate_amount(amount)

        with self.journal.lock():
            self._reload_locked()
            previous = self._transactions.get(transaction_id)
            if previous is not None:
                if (
                    previous["command"] != "credit"
                    or previous["account"] != account
                    or previous["amount"] != amount
                ):
                    raise ConflictingTransactionError(
                        f"Transaction ID {transaction_id!r} was already used differently"
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
        """Transfer funds atomically, rejecting overdrafts before journaling."""
        self._validate_string("transaction_id", transaction_id)
        self._validate_string("source", source)
        self._validate_string("target", target)
        self._validate_amount(amount)

        with self.journal.lock():
            self._reload_locked()
            previous = self._transactions.get(transaction_id)
            if previous is not None:
                if (
                    previous["command"] != "transfer"
                    or previous["source"] != source
                    or previous["target"] != target
                    or previous["amount"] != amount
                ):
                    raise ConflictingTransactionError(
                        f"Transaction ID {transaction_id!r} was already used differently"
                    )
                return dict(previous)

            if source == target:
                raise ValueError("source and target accounts must differ")

            source_balance = self._balances.get(source, 0)
            if source_balance < amount:
                raise InsufficientFundsError(
                    f"Account {source!r} has insufficient funds for transfer"
                )
            target_balance = self._balances.get(target, 0) + amount
            new_source_balance = source_balance - amount

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
                "target_balance": target_balance,
            }
            self._balances[source] = new_source_balance
            self._balances[target] = target_balance
            self._transactions[transaction_id] = result
            self._next_index += 1
            return dict(result)
