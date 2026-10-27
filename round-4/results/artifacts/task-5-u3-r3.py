"""Incremental HTTP/1.1 chunked transfer decoder."""

import re
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


_CHUNK_EXTENSIONS = re.compile(
    rb'(?:[ \t]*;[ \t]*[!#$%&\'*+\-.^_`|~0-9A-Za-z]+'
    rb'(?:[ \t]*=[ \t]*(?:[!#$%&\'*+\-.^_`|~0-9A-Za-z]+'
    rb'|"(?:[\t \x21\x23-\x5b\x5d-\x7e\x80-\xff]|'
    rb'\\[\t \x20-\x7e\x80-\xff])*"))?)*\Z'
)
_HEX_DIGITS = b"0123456789abcdefABCDEF"


class ChunkedDecoder:
    """Decode an HTTP chunked body as arbitrarily fragmented bytes arrive."""

    def __init__(
        self,
        max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
        max_header_bytes: int = DEFAULT_MAX_HEADER_BYTES,
        max_trailer_bytes: int = DEFAULT_MAX_TRAILER_BYTES,
    ):
        self.max_chunk_size = max_chunk_size
        self.max_header_bytes = max_header_bytes
        self.max_trailer_bytes = max_trailer_bytes
        self._state = "size"
        self._buf = b""
        self._chunk_remaining = 0
        self._trailer_raw = bytearray()
        self._trailers: Dict[bytes, bytes] = {}
        self._error = False

    @property
    def done(self) -> bool:
        return self._state == "done"

    @property
    def trailers(self) -> Dict[bytes, bytes]:
        return self._trailers

    def _raise_if_failed(self) -> None:
        if self._error:
            raise ChunkedDecodeError("decoder is in an unrecoverable error state")

    @staticmethod
    def _check_header_controls(data: bytes) -> None:
        for value in data:
            if (value < 0x20 and value != 0x09) or value == 0x7F:
                raise MalformedFramingError("control character in chunk header")

    def _parse_chunk_header(self, line: bytes) -> int:
        self._check_header_controls(line)
        i = 0
        while i < len(line) and line[i] in _HEX_DIGITS:
            i += 1
        if i == 0 or (line[i:] and not _CHUNK_EXTENSIONS.fullmatch(line[i:])):
            raise MalformedFramingError(f"invalid chunk header: {line!r}")

        size = int(line[:i], 16)
        if size > self.max_chunk_size:
            raise SizeLimitExceededError(
                f"Chunk size {size} exceeds maximum {self.max_chunk_size}"
            )
        return size

    def _check_partial_header(self) -> None:
        # A final CR may be the first half of the line delimiter.
        partial = self._buf[:-1] if self._buf.endswith(b"\r") else self._buf
        self._check_header_controls(partial)
        if len(partial) > self.max_header_bytes:
            raise SizeLimitExceededError(
                f"Chunk header exceeds maximum {self.max_header_bytes} bytes"
            )

    def _check_trailer_controls(self, line: bytes) -> None:
        for value in line:
            if (value < 0x20 and value != 0x09) or value == 0x7F:
                raise MalformedFramingError("control character in trailer field")

    def _decode(self) -> bytes:
        output = bytearray()

        while True:
            if self._state == "size":
                end = self._buf.find(b"\r\n")
                if end < 0:
                    self._check_partial_header()
                    break
                if end > self.max_header_bytes:
                    raise SizeLimitExceededError(
                        f"Chunk header exceeds maximum {self.max_header_bytes} bytes"
                    )
                line, self._buf = self._buf[:end], self._buf[end + 2 :]
                size = self._parse_chunk_header(line)
                if size == 0:
                    self._state = "trailers"
                else:
                    self._chunk_remaining = size
                    self._state = "data"

            elif self._state == "data":
                if not self._buf:
                    break
                count = min(self._chunk_remaining, len(self._buf))
                output.extend(self._buf[:count])
                self._buf = self._buf[count:]
                self._chunk_remaining -= count
                if self._chunk_remaining == 0:
                    self._state = "data-crlf"

            elif self._state == "data-crlf":
                if not self._buf:
                    break
                if self._buf[0] != 0x0D:
                    raise MalformedFramingError("missing CRLF after chunk data")
                if len(self._buf) < 2:
                    break
                if self._buf[1] != 0x0A:
                    raise MalformedFramingError("missing CRLF after chunk data")
                self._buf = self._buf[2:]
                self._state = "size"

            elif self._state == "trailers":
                end = self._buf.find(b"\r\n")
                if end < 0:
                    partial = self._buf[:-1] if self._buf.endswith(b"\r") else self._buf
                    if b"\n" in partial or b"\r" in partial:
                        raise MalformedFramingError("invalid line ending in trailers")
                    self._check_trailer_controls(partial)
                    if len(self._trailer_raw) + len(partial) > self.max_trailer_bytes:
                        raise SizeLimitExceededError(
                            f"Trailer section exceeds maximum {self.max_trailer_bytes} bytes"
                        )
                    break

                line = self._buf[:end]
                self._buf = self._buf[end + 2 :]
                if not line:
                    self._trailers = parse_trailers_section(
                        bytes(self._trailer_raw), self.max_trailer_bytes
                    )
                    self._state = "done"
                    if self._buf:
                        raise TrailingDataError("data follows the chunked message")
                    break

                self._check_trailer_controls(line)
                self._trailer_raw.extend(line)
                self._trailer_raw.extend(b"\r\n")
                if len(self._trailer_raw) > self.max_trailer_bytes:
                    raise SizeLimitExceededError(
                        f"Trailer section exceeds maximum {self.max_trailer_bytes} bytes"
                    )

            else:  # completed state
                if self._buf:
                    raise TrailingDataError("data follows the chunked message")
                break

        return bytes(output)

    def feed(self, data: bytes) -> bytes:
        self._raise_if_failed()
        if not isinstance(data, (bytes, bytearray, memoryview)):
            self._error = True
            raise TypeError("feed() requires bytes, bytearray, or memoryview")
        if self.done:
            self._error = True
            raise TrailingDataError("data fed after the chunked message completed")

        try:
            self._buf += bytes(data)
            return self._decode()
        except ChunkedDecodeError:
            self._error = True
            raise

    def finish(self) -> None:
        self._raise_if_failed()
        if self.done:
            return
        self._error = True
        raise IncompleteMessageError("chunked message ended before completion")
