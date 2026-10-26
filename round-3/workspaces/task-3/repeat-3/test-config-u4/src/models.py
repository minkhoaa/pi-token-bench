"""Data models and status definitions for inventory reservations."""

from dataclasses import dataclass
from enum import Enum


class ReservationStatus(str, Enum):
    ACTIVE = "active"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    sku: str
    quantity: int
    status: ReservationStatus

    def to_dict(self) -> dict:
        return {
            "reservation_id": self.reservation_id,
            "sku": self.sku,
            "quantity": self.quantity,
            "status": str(self.status.value),
        }
