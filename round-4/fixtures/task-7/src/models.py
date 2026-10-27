from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Record:
    """Represents a journal entry stored on disk."""
    index: int
    command: str  # "credit" or "transfer"
    tx_id: str
    account: str
    amount: int
    target: Optional[str] = None
    checksum: Optional[str] = None

    def payload_dict(self) -> dict:
        """Return the dictionary used for serialization and hashing."""
        d = {
            "index": self.index,
            "command": self.command,
            "tx_id": self.tx_id,
            "account": self.account,
            "amount": self.amount,
        }
        if self.target is not None:
            d["target"] = self.target
        return d
