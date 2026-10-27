"""Exception hierarchy for HTTP chunked decoder."""


class ChunkedDecodeError(Exception):
    """Base exception for all chunked transfer decoding errors."""
    pass


class MalformedFramingError(ChunkedDecodeError):
    """Raised when chunk framing is malformed or invalid syntax is encountered."""
    pass


class SizeLimitExceededError(ChunkedDecodeError):
    """Raised when chunk size, header size, or trailer section size exceeds limits."""
    pass


class IncompleteMessageError(ChunkedDecodeError):
    """Raised when finish() is invoked before the message has cleanly completed."""
    pass


class TrailingDataError(ChunkedDecodeError):
    """Raised when data is fed to decoder after the chunked stream has completed."""
    pass
