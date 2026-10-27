class LedgerError(Exception):
    """Base exception for all ledger-related errors."""
    pass


class InvalidAmountError(LedgerError):
    """Raised when an amount is not a strictly positive integer, or is of an invalid type."""
    pass


class InsufficientFundsError(LedgerError):
    """Raised when an account does not have enough funds to complete a transfer or debit."""
    pass


class DuplicateTransactionError(LedgerError):
    """Raised when a transaction_id is reused with different parameters or invalid duplicate state."""
    pass


class ConflictingTransactionError(LedgerError):
    """Raised when a transaction_id is reused with conflicting command, account, or amount parameters."""
    pass


class JournalCorruptionError(LedgerError):
    """Raised when journal integrity verification fails (e.g., checksum mismatch or malformed record)."""
    pass
