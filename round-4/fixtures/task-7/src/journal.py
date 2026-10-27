"""
Journal module for crash-safe, append-only event-sourced ledger.
Provides JSON-line framing with SHA-256 checksum per record, fsync durability,
file locking via fcntl.flock, and clean truncated trailing record recovery.
"""

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Generator, List

from .exceptions import JournalCorruptionError
from .models import Record


def compute_record_checksum(record_dict: dict) -> str:
    """Compute sha256 checksum over canonical JSON representation without checksum field."""
    clean = {k: v for k, v in record_dict.items() if k != "checksum"}
    payload = json.dumps(clean, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class Journal:
    """
    Append-only journal file.
    Format per line:
    {"index": 0, "command": "credit", "tx_id": "tx1", "account": "alice", "amount": 100, "checksum": "..."}\n
    """

    def __init__(self, journal_path: Path):
        self.path = Path(journal_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.path.parent / (self.path.name + ".lock")
        self._lock_fd = None

    @contextlib.contextmanager
    def lock(self, exclusive: bool = True) -> Generator[None, None, None]:
        """Acquire process-level flock on .lock file, released on exit."""
        lock_mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o666)
        try:
            fcntl.flock(fd, lock_mode)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def append(self, record: Record) -> None:
        """
        Append a Record to the journal file, calculate checksum, write newline, and fsync.
        Must be called with lock held for concurrency safety.
        """
        payload = record.payload_dict()
        checksum = compute_record_checksum(payload)
        payload["checksum"] = checksum
        line = json.dumps(payload, sort_keys=True) + "\n"

        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def read_records(self) -> List[Record]:
        """
        Read and validate all records from the journal file.
        Recovers cleanly from trailing truncated records at EOF (e.g. from crash mid-write).
        Raises JournalCorruptionError if a record in the middle is corrupt or has invalid checksum.
        """
        if not self.path.exists():
            return []

        records: List[Record] = []
        with open(self.path, "rb") as f:
            raw_lines = f.readlines()

        if not raw_lines:
            return []

        total_lines = len(raw_lines)
        for idx, line in enumerate(raw_lines):
            is_last = (idx == total_lines - 1)
            line_str = line.decode("utf-8", errors="replace").strip()
            if not line_str:
                continue

            try:
                data = json.loads(line_str)
            except Exception:
                if is_last:
                    # Trailing truncated record recovery contract: truncate the corrupt trailing record
                    break
                raise JournalCorruptionError(f"Corrupt JSON line at record {idx}")

            if not isinstance(data, dict) or "checksum" not in data:
                if is_last:
                    break
                raise JournalCorruptionError(f"Record {idx} missing checksum or not a dict")

            stored_checksum = data["checksum"]
            expected_checksum = compute_record_checksum(data)
            if stored_checksum != expected_checksum:
                if is_last:
                    # Trailing incomplete record with checksum mismatch
                    break
                raise JournalCorruptionError(f"Checksum mismatch at record {idx}")

            try:
                rec = Record(
                    index=data["index"],
                    command=data["command"],
                    tx_id=data["tx_id"],
                    account=data["account"],
                    amount=data["amount"],
                    target=data.get("target"),
                    checksum=stored_checksum,
                )
                records.append(rec)
            except Exception as e:
                if is_last:
                    break
                raise JournalCorruptionError(f"Malformed record fields at record {idx}: {e}")

        return records
