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
    """Ledger whose durable journal is the source of truth."""

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
    def _validate_identifier(value: str, name: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")

    @staticmethod
    def _validate_amount(amount: int) -> None:
        if type(amount) is not int or amount <= 0:
            raise InvalidAmountError("amount must be a strictly positive integer")

    def _recover_trailing_record(self) -> None:
        """Remove an incomplete final line and ensure complete records are framed."""
        if not self.journal_path.exists():
            return

        raw = self.journal_path.read_bytes()
        lines = raw.splitlines(keepends=True)
        offset = 0
        trailing_start = None
        for index, line in enumerate(lines):
            if line.strip() and index == len(lines) - 1:
                trailing_start = offset
            offset += len(line)

        if trailing_start is not None:
            line = lines[-1]
            try:
                data = json.loads(line.decode("utf-8", errors="replace").strip())
            except (UnicodeDecodeError, json.JSONDecodeError):
                data = None

            if not isinstance(data, dict) or "checksum" not in data:
                self._truncate_journal(trailing_start)
            elif data["checksum"] != compute_record_checksum(data):
                # A complete JSON record with a bad checksum is tampering, not a
                # recoverable short write. Journal.read_records tolerates this at
                # EOF, so reject it here before delegating record validation.
                raise JournalCorruptionError("Checksum mismatch in final journal record")
            else:
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
                except Exception:
                    # Match Journal's truncated-final-record recovery behavior.
                    self._truncate_journal(trailing_start)

        # Journal.append writes a newline after each record. A complete final
        # record may have survived a crash just before that delimiter.
        if self.journal_path.exists():
            with open(self.journal_path, "r+b") as journal_file:
                journal_file.seek(0, os.SEEK_END)
                if journal_file.tell():
                    journal_file.seek(-1, os.SEEK_END)
                    if journal_file.read(1) != b"\n":
                        journal_file.seek(0, os.SEEK_END)
                        journal_file.write(b"\n")
                        journal_file.flush()
                        os.fsync(journal_file.fileno())

    def _truncate_journal(self, length: int) -> None:
        with open(self.journal_path, "r+b") as journal_file:
            journal_file.truncate(length)
            journal_file.flush()
            os.fsync(journal_file.fileno())

    def _reload_from_journal(self) -> Dict[str, int]:
        """Refresh cached state. Caller must hold the journal lock."""
        self._recover_trailing_record()
        records = self.journal.read_records()
        balances: Dict[str, int] = {}
        transactions: Dict[str, dict] = {}

        try:
            for expected_index, record in enumerate(records):
                if type(record.index) is not int or record.index != expected_index:
                    raise ValueError("journal indexes are not contiguous")
                if (not isinstance(record.tx_id, str) or not record.tx_id
                        or not isinstance(record.account, str) or not record.account
                        or type(record.amount) is not int or record.amount <= 0):
                    raise ValueError("invalid transaction fields")
                if record.tx_id in transactions:
                    raise ValueError("duplicate transaction id in journal")

                if record.command == "credit":
                    if record.target is not None:
                        raise ValueError("credit record has a target")
                    balances[record.account] = balances.get(record.account, 0) + record.amount
                    result = {
                        "status": "success",
                        "command": "credit",
                        "transaction_id": record.tx_id,
                        "account": record.account,
                        "amount": record.amount,
                        "balance": balances[record.account],
                    }
                elif record.command == "transfer":
                    if (not isinstance(record.target, str) or not record.target
                            or record.target == record.account):
                        raise ValueError("invalid transfer target")
                    source_balance = balances.get(record.account, 0)
                    if source_balance < record.amount:
                        raise ValueError("transfer overdrafts its source account")
                    balances[record.account] = source_balance - record.amount
                    balances[record.target] = balances.get(record.target, 0) + record.amount
                    result = {
                        "status": "success",
                        "command": "transfer",
                        "transaction_id": record.tx_id,
                        "source": record.account,
                        "target": record.target,
                        "amount": record.amount,
                        "source_balance": balances[record.account],
                        "target_balance": balances[record.target],
                    }
                else:
                    raise ValueError("unknown journal command")

                transactions[record.tx_id] = result
        except JournalCorruptionError:
            raise
        except Exception as error:
            raise JournalCorruptionError(f"Invalid journal record: {error}") from error

        self._balances = balances
        self._transactions = transactions
        self._next_index = len(records)
        return dict(balances)

    def replay(self) -> Dict[str, int]:
        """Replay the journal and return reconstructed account balances."""
        with self.journal.lock():
            return self._reload_from_journal()

    def balance(self, account: str) -> int:
        """Return an account's latest durable balance, or zero if it is new."""
        self._validate_identifier(account, "account")
        with self.journal.lock():
            self._reload_from_journal()
            return self._balances.get(account, 0)

    def credit(self, transaction_id: str, account: str, amount: int) -> dict:
        """Credit an account once and durably append the transaction."""
        self._validate_identifier(transaction_id, "transaction_id")
        self._validate_identifier(account, "account")
        self._validate_amount(amount)

        with self.journal.lock():
            self._reload_from_journal()
            previous = self._transactions.get(transaction_id)
            if previous is not None:
                if (previous["command"] == "credit"
                        and previous["account"] == account
                        and previous["amount"] == amount):
                    return dict(previous)
                raise ConflictingTransactionError(
                    f"transaction_id {transaction_id!r} was used with different parameters"
                )

            new_balance = self._balances.get(account, 0) + amount
            self.journal.append(Record(
                index=self._next_index,
                command="credit",
                tx_id=transaction_id,
                account=account,
                amount=amount,
            ))
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
        self._validate_identifier(transaction_id, "transaction_id")
        self._validate_identifier(source, "source")
        self._validate_identifier(target, "target")
        self._validate_amount(amount)
        if source == target:
            raise ValueError("source and target must be different accounts")

        with self.journal.lock():
            self._reload_from_journal()
            previous = self._transactions.get(transaction_id)
            if previous is not None:
                if (previous["command"] == "transfer"
                        and previous["source"] == source
                        and previous["target"] == target
                        and previous["amount"] == amount):
                    return dict(previous)
                raise ConflictingTransactionError(
                    f"transaction_id {transaction_id!r} was used with different parameters"
                )

            source_balance = self._balances.get(source, 0)
            if source_balance < amount:
                raise InsufficientFundsError(
                    f"account {source!r} has insufficient funds"
                )
            new_source_balance = source_balance - amount
            new_target_balance = self._balances.get(target, 0) + amount
            self.journal.append(Record(
                index=self._next_index,
                command="transfer",
                tx_id=transaction_id,
                account=source,
                amount=amount,
                target=target,
            ))
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
