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
from src.headers import parse_trailers_section


_TCHARS = frozenset(b"!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
_HEX_DIGITS = {
    **{byte: byte - ord("0") for byte in range(ord("0"), ord("9") + 1)},
    **{byte: byte - ord("a") + 10 for byte in range(ord("a"), ord("f") + 1)},
    **{byte: byte - ord("A") + 10 for byte in range(ord("A"), ord("F") + 1)},
}


def _is_quoted_text(byte: int) -> bool:
    return (
        byte == 0x09
        or byte == 0x20
        or byte == 0x21
        or 0x23 <= byte <= 0x5B
        or 0x5D <= byte <= 0x7E
        or byte >= 0x80
    )


def _is_quoted_pair(byte: int) -> bool:
    return byte == 0x09 or byte == 0x20 or 0x21 <= byte <= 0x7E or byte >= 0x80


def _parse_chunk_header(header: bytes) -> int:
    """Validate a chunk-size line and return its hexadecimal size."""
    digit_end = 0
    while digit_end < len(header) and header[digit_end] in _HEX_DIGITS:
        digit_end += 1
    if digit_end == 0:
        raise MalformedFramingError("Chunk header must begin with a hexadecimal size")

    # Validate the extension grammar without treating extensions as part of the size.
    index = digit_end
    while index < len(header):
        while index < len(header) and header[index] in (0x20, 0x09):
            index += 1
        if index >= len(header) or header[index] != ord(";"):
            raise MalformedFramingError("Invalid chunk extension")
        index += 1
        while index < len(header) and header[index] in (0x20, 0x09):
            index += 1

        name_start = index
        while index < len(header) and header[index] in _TCHARS:
            index += 1
        if index == name_start:
            raise MalformedFramingError("Chunk extension name is missing or invalid")

        while index < len(header) and header[index] in (0x20, 0x09):
            index += 1
        if index < len(header) and header[index] == ord("="):
            index += 1
            while index < len(header) and header[index] in (0x20, 0x09):
                index += 1
            if index >= len(header):
                raise MalformedFramingError("Chunk extension value is missing")

            if header[index] == ord('"'):
                index += 1
                closed = False
                while index < len(header):
                    byte = header[index]
                    if byte == ord('"'):
                        index += 1
                        closed = True
                        break
                    if byte == ord("\\"):
                        index += 1
                        if index >= len(header) or not _is_quoted_pair(header[index]):
                            raise MalformedFramingError("Invalid quoted chunk extension value")
                        index += 1
                    elif _is_quoted_text(byte):
                        index += 1
                    else:
                        raise MalformedFramingError("Invalid control character in chunk extension")
                if not closed:
                    raise MalformedFramingError("Unterminated quoted chunk extension value")
            else:
                value_start = index
                while index < len(header) and header[index] in _TCHARS:
                    index += 1
                if index == value_start:
                    raise MalformedFramingError("Invalid chunk extension value")

        while index < len(header) and header[index] in (0x20, 0x09):
            index += 1
        if index < len(header) and header[index] != ord(";"):
            raise MalformedFramingError("Invalid chunk extension")

    size = 0
    for byte in header[:digit_end]:
        size = size * 16 + _HEX_DIGITS[byte]
    return size


def _validate_partial_line(line: bytearray) -> None:
    """Reject line breaks and controls that cannot form a valid CRLF line."""
    final_cr = len(line) - 1 if line and line[-1] == 0x0D else -1
    for index, byte in enumerate(line):
        if byte == 0x0A or (byte == 0x0D and index != final_cr):
            raise MalformedFramingError("Invalid line ending in chunked framing")
        if byte == 0x7F or (byte < 0x20 and byte not in (0x09, 0x0D)):
            raise MalformedFramingError("Illegal control character in chunked framing")


class ChunkedDecoder:
    """Decode an HTTP/1.1 chunked body incrementally."""

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
        self._done = False
        self._error = None
        self._buf = bytearray()
        self._chunk_remaining = 0
        self._trailer_buffer = bytearray()
        self._trailer_wire_bytes = 0
        self._trailers: Dict[bytes, bytes] = {}

    @property
    def done(self) -> bool:
        return self._done

    @property
    def trailers(self) -> Dict[bytes, bytes]:
        return self._trailers

    def _ensure_healthy(self) -> None:
        if self._error is not None:
            raise ChunkedDecodeError("Decoder is in an unrecoverable error state") from self._error

    def _fail(self, error: ChunkedDecodeError) -> None:
        self._error = error
        raise error

    def feed(self, data: bytes) -> bytes:
        self._ensure_healthy()
        if not isinstance(data, (bytes, bytearray, memoryview)):
            self._error = ChunkedDecodeError("Invalid input type")
            raise TypeError("feed() requires bytes, bytearray, or memoryview")
        if self._done:
            self._fail(TrailingDataError("Data supplied after the chunked message completed"))

        try:
            self._buf.extend(bytes(data))
            out = bytearray()

            while True:
                if self._state == "header":
                    crlf = self._buf.find(b"\r\n")
                    if crlf < 0:
                        _validate_partial_line(self._buf)
                        partial_length = len(self._buf) - (1 if self._buf.endswith(b"\r") else 0)
                        if partial_length > self.max_header_bytes:
                            raise SizeLimitExceededError("Chunk header exceeds configured maximum")
                        break
                    if crlf > self.max_header_bytes:
                        raise SizeLimitExceededError("Chunk header exceeds configured maximum")

                    header = bytes(self._buf[:crlf])
                    del self._buf[: crlf + 2]
                    _validate_partial_line(bytearray(header))
                    chunk_size = _parse_chunk_header(header)
                    if chunk_size > self.max_chunk_size:
                        raise SizeLimitExceededError(
                            f"Chunk size {chunk_size} exceeds configured maximum {self.max_chunk_size}"
                        )
                    if chunk_size == 0:
                        self._state = "trailers"
                        self._trailer_buffer.clear()
                        self._trailer_wire_bytes = 0
                    else:
                        self._chunk_remaining = chunk_size
                        self._state = "data"

                elif self._state == "data":
                    if not self._buf:
                        break
                    count = min(self._chunk_remaining, len(self._buf))
                    out.extend(self._buf[:count])
                    del self._buf[:count]
                    self._chunk_remaining -= count
                    if self._chunk_remaining == 0:
                        self._state = "data_crlf"

                elif self._state == "data_crlf":
                    if not self._buf:
                        break
                    if self._buf[0] != 0x0D:
                        raise MalformedFramingError("Chunk data is not followed by CRLF")
                    if len(self._buf) < 2:
                        break
                    if self._buf[1] != 0x0A:
                        raise MalformedFramingError("Chunk data is not followed by CRLF")
                    del self._buf[:2]
                    self._state = "header"

                elif self._state == "trailers":
                    crlf = self._buf.find(b"\r\n")
                    if crlf < 0:
                        _validate_partial_line(self._buf)
                        if self._trailer_wire_bytes + len(self._buf) > self.max_trailer_bytes:
                            raise SizeLimitExceededError("Trailer section exceeds configured maximum")
                        break

                    line = bytes(self._buf[:crlf])
                    line_wire_size = crlf + 2
                    if self._trailer_wire_bytes + line_wire_size > self.max_trailer_bytes:
                        raise SizeLimitExceededError("Trailer section exceeds configured maximum")
                    del self._buf[:line_wire_size]
                    self._trailer_wire_bytes += line_wire_size

                    if line:
                        self._trailer_buffer.extend(line)
                        self._trailer_buffer.extend(b"\r\n")
                    else:
                        raw_trailers = bytes(self._trailer_buffer[:-2]) if self._trailer_buffer else b""
                        self._trailers = parse_trailers_section(raw_trailers, self.max_trailer_bytes)
                        self._state = "done"
                        self._done = True

                elif self._state == "done":
                    if self._buf:
                        raise TrailingDataError("Unexpected data after the chunked message completed")
                    break

                else:
                    raise ChunkedDecodeError("Invalid decoder state")

            return bytes(out)
        except Exception as error:
            self._error = (
                error if isinstance(error, ChunkedDecodeError)
                else ChunkedDecodeError("Decoder failed while processing input")
            )
            raise

    def finish(self) -> None:
        self._ensure_healthy()
        if self._done:
            return
        error = IncompleteMessageError("Chunked message ended before its terminating trailer CRLF")
        self._error = error
        raise error
