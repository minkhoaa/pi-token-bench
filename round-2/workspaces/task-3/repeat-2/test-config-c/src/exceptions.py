"""Exceptions for the inventory reservation service."""

class InventoryError(Exception):
    """Base exception for all inventory reservation errors."""
    pass


class SkuNotFoundError(InventoryError):
    """Raised when the specified SKU is not found in inventory."""
    pass


class InsufficientStockError(InventoryError):
    """Raised when available inventory is insufficient for the requested quantity."""
    pass


class ReservationNotFoundError(InventoryError):
    """Raised when a reservation ID cannot be found."""
    pass


class InvalidReservationError(InventoryError):
    """Raised when reservation parameters are malformed, invalid, or conflict with existing state."""
    pass
