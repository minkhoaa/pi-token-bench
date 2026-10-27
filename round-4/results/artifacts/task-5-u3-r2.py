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


class ChunkedDecoder:
    """Decode a chunked body incrementally as input arrives."""

    def __init__(
        self,
        max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
        max_header_bytes: int = DEFAULT_MAX_HEADER_BYTES,
        max_trailer_bytes: int = DEFAULT_MAX_TRAILER_BYTES,
    ):
        self.max_chunk_size = max_chunk_size
        self.max_header_bytes = max_header_bytes
        self.max_trailer_bytes = max_trailer_bytes
        self._buf = bytearray()
        self._state = "size"
        self._chunk_remaining = 0
        self._trailer_bytes = bytearray()
        self._trailer_size = 0
        self._trailers: Dict[bytes, bytes] = {}
        self._done = False
        self._failed = False

    @property
    def done(self) -> bool:
        return self._done

    @property
    def trailers(self) -> Dict[bytes, bytes]:
        return self._trailers

    def _check_partial_line(self, limit: int, trailer: bool = False) -> None:
        """Validate a delimiter-free line and enforce its current size limit."""
        if b"\n" in self._buf:
            raise MalformedFramingError("Bare LF in chunk framing")

        cr_pos = self._buf.find(b"\r")
        if cr_pos != -1 and cr_pos != len(self._buf) - 1:
            raise MalformedFramingError("Bare CR in chunk framing")

        if trailer:
            # The helper's trailer block excludes the final empty-line CRLF.
            pending_size = 0 if self._buf == b"\r" else len(self._buf)
            if self._trailer_size + pending_size > limit:
                raise SizeLimitExceededError(
                    f"Trailer section exceeds maximum {limit} bytes"
                )
        else:
            # A final CR may be the first half of the delimiter, not line content.
            line_size = len(self._buf) - (1 if cr_pos == len(self._buf) - 1 else 0)
            if line_size > limit:
                raise SizeLimitExceededError(
                    f"Chunk header exceeds maximum {limit} bytes"
                )

    @staticmethod
    def _is_token_char(char: int) -> bool:
        return (
            48 <= char <= 57
            or 65 <= char <= 90
            or 97 <= char <= 122
            or char in b"!#$%&'*+-.^_`|~"
        )

    @classmethod
    def _validate_extensions(cls, extension: bytes) -> None:
        """Validate chunk-ext syntax and reject control characters."""
        for char in extension:
            if (char < 0x20 and char != 0x09) or char == 0x7F:
                raise MalformedFramingError(
                    "Control character in chunk extension"
                )

        i = 0
        length = len(extension)
        while i < length:
            # BWS before the next semicolon.
            while i < length and extension[i] in (0x20, 0x09):
                i += 1
            if i == length or extension[i] != ord(";"):
                raise MalformedFramingError("Malformed chunk extension")
            i += 1

            while i < length and extension[i] in (0x20, 0x09):
                i += 1
            name_start = i
            while i < length and cls._is_token_char(extension[i]):
                i += 1
            if i == name_start:
                raise MalformedFramingError("Missing chunk extension name")

            while i < length and extension[i] in (0x20, 0x09):
                i += 1
            if i < length and extension[i] == ord("="):
                i += 1
                while i < length and extension[i] in (0x20, 0x09):
                    i += 1
                if i == length:
                    raise MalformedFramingError("Missing chunk extension value")

                if extension[i] == ord('"'):
                    i += 1
                    closed = False
                    while i < length:
                        char = extension[i]
                        if char == ord('"'):
                            i += 1
                            closed = True
                            break
                        if char == ord("\\"):
                            i += 1
                            if i == length:
                                raise MalformedFramingError(
                                    "Incomplete escape in chunk extension"
                                )
                        i += 1
                    if not closed:
                        raise MalformedFramingError(
                            "Unterminated quoted chunk extension"
                        )
                else:
                    value_start = i
                    while i < length and cls._is_token_char(extension[i]):
                        i += 1
                    if i == value_start:
                        raise MalformedFramingError(
                            "Invalid chunk extension value"
                        )

            if i == length:
                return
            if extension[i] not in (0x20, 0x09, ord(";")):
                raise MalformedFramingError("Malformed chunk extension")
            # Whitespace is only valid as BWS before another extension.
            if extension[i] in (0x20, 0x09):
                while i < length and extension[i] in (0x20, 0x09):
                    i += 1
                if i == length:
                    raise MalformedFramingError("Trailing whitespace in chunk extension")
                if extension[i] != ord(";"):
                    raise MalformedFramingError("Malformed chunk extension")

    def _parse_chunk_header(self, line: bytes) -> int:
        if not line:
            raise MalformedFramingError("Empty chunk-size line")

        semicolon = line.find(b";")
        if semicolon == -1:
            size_text = line
            extension = b""
        else:
            size_text = line[:semicolon].rstrip(b" \t")
            extension = line[semicolon:]
            self._validate_extensions(extension)

        if not size_text or any(
            char not in b"0123456789abcdefABCDEF" for char in size_text
        ):
            raise MalformedFramingError("Invalid hexadecimal chunk size")

        chunk_size = int(size_text, 16)
        if chunk_size > self.max_chunk_size:
            raise SizeLimitExceededError(
                f"Chunk size {chunk_size} exceeds maximum {self.max_chunk_size}"
            )
        return chunk_size

    def feed(self, data: bytes) -> bytes:
        if self._failed:
            raise ChunkedDecodeError("Decoder is in an error state")
        if not isinstance(data, (bytes, bytearray, memoryview)):
            self._failed = True
            raise TypeError("feed() requires bytes, bytearray, or memoryview")

        if self._done:
            self._failed = True
            raise TrailingDataError("Data fed after chunked message completed")

        self._buf.extend(bytes(data))
        output = bytearray()

        try:
            while True:
                if self._state == "size":
                    end = self._buf.find(b"\r\n")
                    if end == -1:
                        self._check_partial_line(self.max_header_bytes)
                        break
                    if end > self.max_header_bytes:
                        raise SizeLimitExceededError(
                            f"Chunk header exceeds maximum {self.max_header_bytes} bytes"
                        )

                    line = bytes(self._buf[:end])
                    del self._buf[: end + 2]
                    chunk_size = self._parse_chunk_header(line)
                    if chunk_size == 0:
                        self._state = "trailers"
                    else:
                        self._chunk_remaining = chunk_size
                        self._state = "data"
                    continue

                if self._state == "data":
                    if not self._buf:
                        break
                    count = min(self._chunk_remaining, len(self._buf))
                    output.extend(self._buf[:count])
                    del self._buf[:count]
                    self._chunk_remaining -= count
                    if self._chunk_remaining == 0:
                        self._state = "data_crlf"
                    continue

                if self._state == "data_crlf":
                    if not self._buf:
                        break
                    if self._buf[0] != 0x0D:
                        raise MalformedFramingError(
                            "Missing CRLF after chunk data"
                        )
                    if len(self._buf) == 1:
                        break
                    if self._buf[1] != 0x0A:
                        raise MalformedFramingError(
                            "Missing CRLF after chunk data"
                        )
                    del self._buf[:2]
                    self._state = "size"
                    continue

                if self._state == "trailers":
                    end = self._buf.find(b"\r\n")
                    if end == -1:
                        self._check_partial_line(
                            self.max_trailer_bytes, trailer=True
                        )
                        break

                    section_bytes = end + 2
                    line = bytes(self._buf[:end])
                    del self._buf[:section_bytes]

                    if line:
                        if self._trailer_size + section_bytes > self.max_trailer_bytes:
                            raise SizeLimitExceededError(
                                f"Trailer section exceeds maximum {self.max_trailer_bytes} bytes"
                            )
                        self._trailer_size += section_bytes
                        self._trailer_bytes.extend(line)
                        self._trailer_bytes.extend(b"\r\n")
                        continue

                    self._trailers = parse_trailers_section(
                        bytes(self._trailer_bytes), self.max_trailer_bytes
                    )
                    self._done = True
                    self._state = "done"
                    continue

                if self._state == "done":
                    if self._buf:
                        raise TrailingDataError(
                            "Unexpected data after chunked message"
                        )
                    break

                raise MalformedFramingError("Invalid decoder state")

        except ChunkedDecodeError:
            self._failed = True
            raise

        return bytes(output)

    def finish(self) -> None:
        if self._failed:
            raise ChunkedDecodeError("Decoder is in an error state")
        if self._done:
            return
        self._failed = True
        raise IncompleteMessageError("Chunked message ended before completion")
