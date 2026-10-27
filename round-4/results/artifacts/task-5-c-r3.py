"""Incremental HTTP/1.1 chunked transfer decoder."""

from typing import Dict

from src.constants import (
    DEFAULT_MAX_CHUNK_SIZE,
    DEFAULT_MAX_HEADER_BYTES,
    DEFAULT_MAX_TRAILER_BYTES,
)
from src.exceptions import (
    ChunkedDecodeError,
    IncompleteMessageError,
    MalformedFramingError,
    SizeLimitExceededError,
    TrailingDataError,
)
from src.headers import parse_trailer_line, parse_trailers_section


_HEX_DIGITS = b"0123456789abcdefABCDEF"
_TCHARS = frozenset(b"!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _is_ows(value: int) -> bool:
    return value in (0x20, 0x09)


def _parse_extensions(extensions: bytes) -> None:
    """Validate chunk-ext syntax (RFC 9112 token/quoted-string forms)."""
    pos = 0
    while pos < len(extensions):
        while pos < len(extensions) and _is_ows(extensions[pos]):
            pos += 1
        if pos >= len(extensions) or extensions[pos] != ord(";"):
            raise MalformedFramingError("Malformed chunk extension")
        pos += 1
        while pos < len(extensions) and _is_ows(extensions[pos]):
            pos += 1

        start = pos
        while pos < len(extensions) and extensions[pos] in _TCHARS:
            pos += 1
        if pos == start:
            raise MalformedFramingError("Chunk extension name must be a token")

        while pos < len(extensions) and _is_ows(extensions[pos]):
            pos += 1
        if pos < len(extensions) and extensions[pos] == ord("="):
            pos += 1
            while pos < len(extensions) and _is_ows(extensions[pos]):
                pos += 1
            if pos >= len(extensions):
                raise MalformedFramingError("Chunk extension value is missing")

            if extensions[pos] == ord('"'):
                pos += 1
                closed = False
                while pos < len(extensions):
                    value = extensions[pos]
                    if value == ord('"'):
                        pos += 1
                        closed = True
                        break
                    if value == ord("\\"):
                        pos += 1
                        if pos >= len(extensions):
                            raise MalformedFramingError("Invalid escape in chunk extension")
                        escaped = extensions[pos]
                        if not (_is_ows(escaped) or 0x21 <= escaped <= 0x7e or escaped >= 0x80):
                            raise MalformedFramingError("Illegal control character in chunk extension")
                    elif not (_is_ows(value) or 0x21 <= value <= 0x7e or value >= 0x80):
                        raise MalformedFramingError("Illegal control character in chunk extension")
                    pos += 1
                if not closed:
                    raise MalformedFramingError("Unterminated quoted chunk extension")
            else:
                start = pos
                while pos < len(extensions) and extensions[pos] in _TCHARS:
                    pos += 1
                if pos == start:
                    raise MalformedFramingError("Invalid chunk extension value")

        after_item = pos
        while pos < len(extensions) and _is_ows(extensions[pos]):
            pos += 1
        if pos == len(extensions) and pos != after_item:
            raise MalformedFramingError("Trailing whitespace in chunk extension")
        if pos < len(extensions) and extensions[pos] != ord(";"):
            raise MalformedFramingError("Malformed chunk extension")


def _parse_chunk_header(header: bytes, max_chunk_size: int) -> int:
    # Chunk headers cannot contain controls, apart from HTAB used as BWS in extensions.
    if any((byte < 0x20 and byte != 0x09) or byte == 0x7f for byte in header):
        raise MalformedFramingError("Illegal control character in chunk header")

    pos = 0
    size = 0
    while pos < len(header) and header[pos] in _HEX_DIGITS:
        byte = header[pos]
        if byte >= ord("A"):
            digit = (byte | 0x20) - ord("a") + 10
        else:
            digit = byte - ord("0")
        size = size * 16 + digit
        if size > max_chunk_size:
            raise SizeLimitExceededError(
                f"Chunk size {size} exceeds maximum {max_chunk_size}"
            )
        pos += 1

    if pos == 0:
        raise MalformedFramingError("Chunk size must contain hexadecimal digits")
    if pos < len(header):
        _parse_extensions(header[pos:])
    return size


class ChunkedDecoder:
    """Decode an HTTP chunked body as input arrives."""

    def __init__(
        self,
        max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
        max_header_bytes: int = DEFAULT_MAX_HEADER_BYTES,
        max_trailer_bytes: int = DEFAULT_MAX_TRAILER_BYTES,
    ):
        self.max_chunk_size = max_chunk_size
        self.max_header_bytes = max_header_bytes
        self.max_trailer_bytes = max_trailer_bytes
        self._state = "header"
        self._failed = False
        self._buf = bytearray()
        self._remaining = 0
        self._trailer_bytes = bytearray()
        self._trailers: Dict[bytes, bytes] = {}

    @property
    def done(self) -> bool:
        return self._state == "done"

    @property
    def trailers(self) -> Dict[bytes, bytes]:
        return dict(self._trailers)

    def _check_not_failed(self) -> None:
        if self._failed:
            raise ChunkedDecodeError("decoder is in an unrecoverable error state")

    def _check_partial_header(self) -> None:
        if b"\n" in self._buf:
            raise MalformedFramingError("Bare LF in chunk header")
        if b"\r" in self._buf[:-1]:
            raise MalformedFramingError("Bare CR in chunk header")
        partial = self._buf[:-1] if self._buf.endswith(b"\r") else self._buf
        if any((byte < 0x20 and byte != 0x09) or byte == 0x7f for byte in partial):
            raise MalformedFramingError("Illegal control character in chunk header")
        pending_length = len(partial)
        if pending_length > self.max_header_bytes:
            raise SizeLimitExceededError("Chunk header exceeds configured limit")

    def _check_partial_trailer(self) -> None:
        if b"\n" in self._buf:
            raise MalformedFramingError("Bare LF in trailer section")
        if b"\r" in self._buf[:-1]:
            raise MalformedFramingError("Bare CR in trailer section")
        partial = self._buf[:-1] if self._buf.endswith(b"\r") else self._buf
        if any((byte < 0x20 and byte != 0x09) or byte == 0x7f for byte in partial):
            raise MalformedFramingError("Illegal control character in trailer section")
        if not self._buf or self._buf == b"\r":
            return
        # A nonempty trailer line also contributes its terminating CRLF to the
        # raw section passed to parse_trailers_section().
        pending_length = len(self._buf) + (1 if self._buf.endswith(b"\r") else 2)
        if len(self._trailer_bytes) + pending_length > self.max_trailer_bytes:
            raise SizeLimitExceededError("Trailer section exceeds configured limit")

    def feed(self, data: bytes) -> bytes:
        self._check_not_failed()
        if self._state == "done":
            self._failed = True
            raise TrailingDataError("Data fed after the chunked message completed")
        if not isinstance(data, (bytes, bytearray, memoryview)):
            self._failed = True
            raise TypeError("feed() requires bytes, bytearray, or memoryview")

        try:
            self._buf.extend(bytes(data))
            output = bytearray()

            while True:
                if self._state == "header":
                    delimiter = self._buf.find(b"\r\n")
                    if delimiter < 0:
                        self._check_partial_header()
                        break
                    if delimiter > self.max_header_bytes:
                        raise SizeLimitExceededError("Chunk header exceeds configured limit")
                    header = bytes(self._buf[:delimiter])
                    del self._buf[: delimiter + 2]
                    chunk_size = _parse_chunk_header(header, self.max_chunk_size)
                    if chunk_size == 0:
                        self._state = "trailers"
                    else:
                        self._remaining = chunk_size
                        self._state = "data"

                elif self._state == "data":
                    if not self._buf:
                        break
                    count = min(self._remaining, len(self._buf))
                    output.extend(self._buf[:count])
                    del self._buf[:count]
                    self._remaining -= count
                    if self._remaining == 0:
                        self._state = "data_crlf"

                elif self._state == "data_crlf":
                    if not self._buf:
                        break
                    if self._buf[0] != ord("\r"):
                        raise MalformedFramingError("Missing CRLF after chunk data")
                    if len(self._buf) == 1:
                        break
                    if self._buf[1] != ord("\n"):
                        raise MalformedFramingError("Missing CRLF after chunk data")
                    del self._buf[:2]
                    self._state = "header"

                elif self._state == "trailers":
                    delimiter = self._buf.find(b"\r\n")
                    if delimiter < 0:
                        self._check_partial_trailer()
                        break
                    line = bytes(self._buf[:delimiter])
                    del self._buf[: delimiter + 2]
                    if not line:
                        self._trailers = parse_trailers_section(
                            bytes(self._trailer_bytes), self.max_trailer_bytes
                        )
                        self._state = "done"
                        if self._buf:
                            raise TrailingDataError(
                                "Data follows the terminating trailer CRLF"
                            )
                        break

                    if len(self._trailer_bytes) + len(line) + 2 > self.max_trailer_bytes:
                        raise SizeLimitExceededError("Trailer section exceeds configured limit")
                    parse_trailer_line(line)
                    self._trailer_bytes.extend(line)
                    self._trailer_bytes.extend(b"\r\n")

                elif self._state == "done":
                    if self._buf:
                        raise TrailingDataError("Data follows the terminating trailer CRLF")
                    break

                else:  # Defensive guard; states are internal and fixed.
                    raise ChunkedDecodeError("decoder entered an invalid state")

            return bytes(output)
        except ChunkedDecodeError:
            self._failed = True
            raise
        except (TypeError, ValueError) as exc:
            # Invalid memoryview instances cannot be consumed; do not allow a
            # retry against partially mutated decoder state.
            self._failed = True
            raise TypeError("feed() could not read the supplied bytes-like object") from exc

    def finish(self) -> None:
        self._check_not_failed()
        if self.done:
            return
        self._failed = True
        raise IncompleteMessageError("chunked message ended before completion")
