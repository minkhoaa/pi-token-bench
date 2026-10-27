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


def _is_token_byte(value: int) -> bool:
    return (
        48 <= value <= 57
        or 65 <= value <= 90
        or 97 <= value <= 122
        or value in b"!#$%&'*+-.^_`|~"
    )


def _skip_ows(data: bytes, index: int) -> int:
    while index < len(data) and data[index] in (0x20, 0x09):
        index += 1
    return index


def _parse_chunk_extensions(data: bytes) -> None:
    """Validate the chunk-ext grammar without interpreting extension values."""
    index = 0
    while index < len(data):
        index = _skip_ows(data, index)
        if index == len(data):
            break
        if data[index] != ord(";"):
            raise MalformedFramingError("Invalid chunk extension delimiter")
        index = _skip_ows(data, index + 1)

        start = index
        while index < len(data) and _is_token_byte(data[index]):
            index += 1
        if index == start:
            raise MalformedFramingError("Missing chunk extension name")

        index = _skip_ows(data, index)
        if index < len(data) and data[index] == ord("="):
            index = _skip_ows(data, index + 1)
            if index == len(data):
                raise MalformedFramingError("Missing chunk extension value")

            if data[index] == ord('"'):
                index += 1
                closed = False
                while index < len(data):
                    value = data[index]
                    if value == ord('"'):
                        index += 1
                        closed = True
                        break
                    if value == ord("\\"):
                        index += 1
                        if index == len(data):
                            break
                        escaped = data[index]
                        if not (
                            escaped in (0x09, 0x20)
                            or 0x21 <= escaped <= 0x7E
                            or escaped >= 0x80
                        ):
                            raise MalformedFramingError(
                                "Invalid quoted chunk extension value"
                            )
                        index += 1
                        continue
                    if not (
                        value in (0x09, 0x20, 0x21)
                        or 0x23 <= value <= 0x5B
                        or 0x5D <= value <= 0x7E
                        or value >= 0x80
                    ):
                        raise MalformedFramingError(
                            "Invalid quoted chunk extension value"
                        )
                    index += 1
                if not closed:
                    raise MalformedFramingError("Unterminated chunk extension value")
            else:
                start = index
                while index < len(data) and _is_token_byte(data[index]):
                    index += 1
                if index == start:
                    raise MalformedFramingError("Invalid chunk extension value")

        index = _skip_ows(data, index)
        if index < len(data) and data[index] != ord(";"):
            raise MalformedFramingError("Invalid chunk extension syntax")


class ChunkedDecoder:
    """Decode an HTTP chunked body from arbitrarily fragmented input."""

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
        self._trailer_data = bytearray()
        self._trailers: Dict[bytes, bytes] = {}
        self._done = False
        self._failed = False

    @property
    def done(self) -> bool:
        return self._done

    @property
    def trailers(self) -> Dict[bytes, bytes]:
        return dict(self._trailers)

    @staticmethod
    def _check_partial_line(data: bytearray, context: str) -> None:
        for index, value in enumerate(data):
            if value == 0x0A or (value == 0x0D and index != len(data) - 1):
                raise MalformedFramingError(f"Invalid line ending in {context}")

    def _parse_chunk_header(self, header: bytes) -> int:
        if not header:
            raise MalformedFramingError("Empty chunk-size line")
        if any((value < 0x20 and value != 0x09) or value == 0x7F for value in header):
            raise MalformedFramingError("Control character in chunk-size line")

        semicolon = header.find(b";")
        if semicolon == -1:
            size_field = header
        else:
            size_field = header[:semicolon].rstrip(b" \t")
            _parse_chunk_extensions(header[semicolon:])

        if not size_field or any(
            not (48 <= value <= 57 or 65 <= value <= 70 or 97 <= value <= 102)
            for value in size_field
        ):
            raise MalformedFramingError("Invalid hexadecimal chunk size")

        chunk_size = 0
        for value in size_field:
            if 48 <= value <= 57:
                digit = value - 48
            elif 65 <= value <= 70:
                digit = value - 55
            else:
                digit = value - 87
            chunk_size = chunk_size * 16 + digit
            if chunk_size > self.max_chunk_size:
                raise SizeLimitExceededError(
                    f"Chunk size {chunk_size} exceeds maximum {self.max_chunk_size}"
                )
        return chunk_size

    def feed(self, data: bytes) -> bytes:
        if self._failed:
            raise ChunkedDecodeError("Decoder is in an unrecoverable error state")
        if not isinstance(data, (bytes, bytearray, memoryview)):
            self._failed = True
            raise TypeError("feed() requires bytes, bytearray, or memoryview")

        try:
            if self._done:
                raise TrailingDataError("Data fed after chunked message completion")

            self._buf.extend(bytes(data))
            output = bytearray()

            while True:
                if self._state == "size":
                    end = self._buf.find(b"\r\n")
                    if end == -1:
                        self._check_partial_line(self._buf, "chunk-size line")
                        content_length = len(self._buf) - self._buf.endswith(b"\r")
                        if content_length > self.max_header_bytes:
                            raise SizeLimitExceededError(
                                "Chunk-size line exceeds configured maximum"
                            )
                        break

                    header = bytes(self._buf[:end])
                    del self._buf[: end + 2]
                    if len(header) > self.max_header_bytes:
                        raise SizeLimitExceededError(
                            "Chunk-size line exceeds configured maximum"
                        )
                    self._chunk_remaining = self._parse_chunk_header(header)
                    if self._chunk_remaining == 0:
                        self._state = "trailers"
                    else:
                        self._state = "data"

                elif self._state == "data":
                    if not self._buf:
                        break
                    count = min(self._chunk_remaining, len(self._buf))
                    output.extend(self._buf[:count])
                    del self._buf[:count]
                    self._chunk_remaining -= count
                    if self._chunk_remaining == 0:
                        self._state = "data_crlf"

                elif self._state == "data_crlf":
                    if not self._buf:
                        break
                    if self._buf[0] != 0x0D:
                        raise MalformedFramingError("Missing CRLF after chunk data")
                    if len(self._buf) == 1:
                        break
                    if self._buf[1] != 0x0A:
                        raise MalformedFramingError("Missing CRLF after chunk data")
                    del self._buf[:2]
                    self._state = "size"

                elif self._state == "trailers":
                    end = self._buf.find(b"\r\n")
                    if end == -1:
                        self._check_partial_line(self._buf, "trailer section")
                        line_length = len(self._buf) - self._buf.endswith(b"\r")
                        if line_length:
                            minimum_size = len(self._trailer_data) + line_length + 2
                            if minimum_size > self.max_trailer_bytes:
                                raise SizeLimitExceededError(
                                    "Trailer section exceeds configured maximum"
                                )
                        elif len(self._trailer_data) > self.max_trailer_bytes:
                            raise SizeLimitExceededError(
                                "Trailer section exceeds configured maximum"
                            )
                        break

                    line = bytes(self._buf[:end])
                    if b"\r" in line or b"\n" in line:
                        raise MalformedFramingError("Invalid line ending in trailer section")
                    del self._buf[: end + 2]

                    if not line:
                        self._trailers = parse_trailers_section(
                            bytes(self._trailer_data), self.max_trailer_bytes
                        )
                        self._done = True
                        self._state = "done"
                        if self._buf:
                            raise TrailingDataError(
                                "Data follows chunked message terminator"
                            )
                        break

                    self._trailer_data.extend(line + b"\r\n")
                    if len(self._trailer_data) > self.max_trailer_bytes:
                        raise SizeLimitExceededError(
                            "Trailer section exceeds configured maximum"
                        )

                else:
                    if self._buf:
                        raise TrailingDataError("Data follows chunked message terminator")
                    break

            return bytes(output)
        except ChunkedDecodeError:
            self._failed = True
            raise

    def finish(self) -> None:
        if self._failed:
            raise ChunkedDecodeError("Decoder is in an unrecoverable error state")
        if self._done:
            return
        self._failed = True
        raise IncompleteMessageError("Chunked message ended before completion")
