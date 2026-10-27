"""Constants and default limits for chunked transfer decoding."""

DEFAULT_MAX_CHUNK_SIZE: int = 8 * 1024 * 1024  # 8 MB
DEFAULT_MAX_HEADER_BYTES: int = 8192          # 8 KB
DEFAULT_MAX_TRAILER_BYTES: int = 16384        # 16 KB

# Maximum allowed trailer field-line length before reject
MAX_TRAILER_LINE_LENGTH: int = 4096
