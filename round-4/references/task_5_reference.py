"""Reference implementation of ChunkedDecoder passing all public and hidden tests."""

from typing import Dict, Optional
from src.constants import (
    DEFAULT_MAX_CHUNK_SIZE,
    DEFAULT_MAX_HEADER_BYTES,
    DEFAULT_MAX_TRAILER_BYTES,
)
from src.exceptions import (
    IncompleteMessageError,
    MalformedFramingError,
    SizeLimitExceededError,
    TrailingDataError,
)
from src.headers import parse_trailers_section

# Decoder state machine states
STATE_READ_CHUNK_HEADER = 0
STATE_READ_CHUNK_DATA = 1
STATE_READ_CHUNK_CRLF = 2
STATE_READ_TRAILER_LINE = 3
STATE_DONE = 4
STATE_ERROR = 5


class ChunkedDecoder:
    """
    Streaming HTTP/1.1 Chunked Transfer Decoder adhering to RFC 9112 Section 7.1.
    """

    def __init__(
        self,
        max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
        max_header_bytes: int = DEFAULT_MAX_HEADER_BYTES,
        max_trailer_bytes: int = DEFAULT_MAX_TRAILER_BYTES,
    ):
        self.max_chunk_size = max_chunk_size
        self.max_header_bytes = max_header_bytes
        self.max_trailer_bytes = max_trailer_bytes

        self._state: int = STATE_READ_CHUNK_HEADER
        self._buffer: bytearray = bytearray()
        self._current_chunk_size: int = 0
        self._current_chunk_remaining: int = 0
        self._trailer_buffer: bytearray = bytearray()
        self._trailers: Dict[bytes, bytes] = {}

    @property
    def done(self) -> bool:
        """Returns True if the final zero-length chunk and trailers have been fully received."""
        return self._state == STATE_DONE

    @property
    def trailers(self) -> Dict[bytes, bytes]:
        """Returns dictionary of parsed trailers if done."""
        return dict(self._trailers)

    def feed(self, data: bytes) -> bytes:
        """
        Feed a slice of incoming chunked-encoded bytes.
        Returns decoded payload bytes decoded during this call.
        """
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError(f"feed() argument must be bytes, not {type(data).__name__}")

        if self._state == STATE_DONE:
            if len(data) > 0:
                self._state = STATE_ERROR
                raise TrailingDataError("Received data after chunked stream completion")
            return b""

        if self._state == STATE_ERROR:
            raise MalformedFramingError("Decoder is in an unrecoverable error state")

        if not data:
            return b""

        self._buffer.extend(data)
        output = bytearray()

        try:
            while self._buffer:
                if self._state == STATE_READ_CHUNK_HEADER:
                    # Look for CRLF terminating chunk header
                    crlf_pos = self._buffer.find(b"\r\n")
                    if crlf_pos == -1:
                        # No CRLF yet
                        if len(self._buffer) > self.max_header_bytes:
                            raise SizeLimitExceededError(
                                f"Chunk header size exceeds limit of {self.max_header_bytes} bytes"
                            )
                        break  # Wait for more data

                    header_line = bytes(self._buffer[:crlf_pos])
                    del self._buffer[: crlf_pos + 2]

                    if len(header_line) > self.max_header_bytes:
                        raise SizeLimitExceededError(
                            f"Chunk header size exceeds limit of {self.max_header_bytes} bytes"
                        )

                    # Parse chunk-size [ chunk-ext ]
                    # RFC 9112 Section 7.1: chunk-size = 1*HEXDIG
                    # chunk-ext = *( BWS ";" BWS chunk-ext-name [ BWS "=" BWS chunk-ext-val ] )
                    parts = header_line.split(b";", 1)
                    raw_size_str = parts[0].strip()

                    if not raw_size_str:
                        raise MalformedFramingError("Empty chunk size in header")

                    # Verify hex string strictly
                    try:
                        chunk_size = int(raw_size_str, 16)
                    except ValueError:
                        raise MalformedFramingError(f"Invalid hex chunk size: {raw_size_str!r}")

                    if chunk_size < 0:
                        raise MalformedFramingError("Negative chunk size")

                    # Reject any non-hex characters (e.g. "+", spaces that int() might allow)
                    for b in raw_size_str:
                        if not (
                            (0x30 <= b <= 0x39)  # 0-9
                            or (0x41 <= b <= 0x46)  # A-F
                            or (0x61 <= b <= 0x66)  # a-f
                        ):
                            raise MalformedFramingError(f"Non-hex character in chunk size: {raw_size_str!r}")

                    # Validate chunk extensions if present
                    if len(parts) > 1:
                        ext_part = parts[1]
                        # Disallow NUL or control characters in extensions
                        for b in ext_part:
                            if b < 0x20 and b != 0x09:
                                raise MalformedFramingError(f"Invalid control char in chunk extension: {b:#x}")

                    if chunk_size > self.max_chunk_size:
                        raise SizeLimitExceededError(
                            f"Chunk size {chunk_size} exceeds maximum {self.max_chunk_size}"
                        )

                    self._current_chunk_size = chunk_size
                    self._current_chunk_remaining = chunk_size

                    if chunk_size == 0:
                        # Last chunk; now reading trailers
                        self._state = STATE_READ_TRAILER_LINE
                    else:
                        self._state = STATE_READ_CHUNK_DATA

                elif self._state == STATE_READ_CHUNK_DATA:
                    needed = self._current_chunk_remaining
                    available = len(self._buffer)
                    take = min(available, needed)

                    output.extend(self._buffer[:take])
                    del self._buffer[:take]
                    self._current_chunk_remaining -= take

                    if self._current_chunk_remaining == 0:
                        self._state = STATE_READ_CHUNK_CRLF

                elif self._state == STATE_READ_CHUNK_CRLF:
                    # Expect CRLF following chunk data
                    if len(self._buffer) < 2:
                        # Need at least 2 bytes
                        if len(self._buffer) == 1 and self._buffer[0] != 0x0D:
                            raise MalformedFramingError(
                                f"Expected CR after chunk data, got {self._buffer[0]:#x}"
                            )
                        break  # Wait for full CRLF

                    if self._buffer[0] != 0x0D or self._buffer[1] != 0x0A:
                        raise MalformedFramingError(
                            f"Expected CRLF after chunk data, got {bytes(self._buffer[:2])!r}"
                        )

                    del self._buffer[:2]
                    self._state = STATE_READ_CHUNK_HEADER

                elif self._state == STATE_READ_TRAILER_LINE:
                    # Reading trailers until an empty line (CRLF) is encountered
                    crlf_pos = self._buffer.find(b"\r\n")
                    if crlf_pos == -1:
                        if len(self._trailer_buffer) + len(self._buffer) > self.max_trailer_bytes:
                            raise SizeLimitExceededError(
                                f"Trailer section exceeds limit of {self.max_trailer_bytes} bytes"
                            )
                        break

                    line = bytes(self._buffer[:crlf_pos])
                    del self._buffer[: crlf_pos + 2]

                    if len(line) == 0:
                        # Empty line marks end of trailer section and end of chunked stream!
                        self._trailers = parse_trailers_section(
                            bytes(self._trailer_buffer), self.max_trailer_bytes
                        )
                        self._state = STATE_DONE

                        # Check if any extra bytes were sent in the buffer
                        if len(self._buffer) > 0:
                            self._state = STATE_ERROR
                            raise TrailingDataError("Received data after final chunk CRLF")
                        break
                    else:
                        if len(self._trailer_buffer) + len(line) + 2 > self.max_trailer_bytes:
                            raise SizeLimitExceededError(
                                f"Trailer section exceeds limit of {self.max_trailer_bytes} bytes"
                            )
                        if self._trailer_buffer:
                            self._trailer_buffer.extend(b"\r\n")
                        self._trailer_buffer.extend(line)

                elif self._state == STATE_DONE:
                    if self._buffer:
                        self._state = STATE_ERROR
                        raise TrailingDataError("Received data after chunked stream completion")
                    break

        except Exception:
            self._state = STATE_ERROR
            raise

        return bytes(output)

    def finish(self) -> None:
        """
        Notify decoder that input stream has closed.
        Raises IncompleteMessageError if stream ended before final chunk and trailers were read.
        """
        if self._state == STATE_ERROR:
            raise MalformedFramingError("Decoder is in an unrecoverable error state")

        if self._state != STATE_DONE:
            self._state = STATE_ERROR
            raise IncompleteMessageError("Stream closed before chunked message completed")
