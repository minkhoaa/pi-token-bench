"""Incremental HTTP/1.1 chunked transfer decoder (RFC 9112, section 7.1)."""

from typing import Dict

from src.constants import (
    DEFAULT_MAX_CHUNK_SIZE,
    DEFAULT_MAX_HEADER_BYTES,
    DEFAULT_MAX_TRAILER_BYTES,
    MAX_TRAILER_LINE_LENGTH,
)
from src.exceptions import (
    ChunkedDecodeError,
    IncompleteMessageError,
    MalformedFramingError,
    SizeLimitExceededError,
    TrailingDataError,
)
from src.headers import parse_trailers_section


_HEX_DIGITS = b"0123456789abcdefABCDEF"
_TCHARS = b"!#$%&'*+-.^_`|~"
_OWS = (0x20, 0x09)


def _is_token_char(value: int) -> bool:
    return (
        48 <= value <= 57
        or 65 <= value <= 90
        or 97 <= value <= 122
        or value in _TCHARS
    )


def _skip_ows(data: bytes, index: int) -> int:
    while index < len(data) and data[index] in _OWS:
        index += 1
    return index


def _parse_quoted_extension(data: bytes, index: int) -> int:
    """Parse a quoted-string starting at index and return its end position."""
    index += 1  # opening quote
    while index < len(data):
        value = data[index]
        if value == 0x22:  # closing quote
            return index + 1
        if value == 0x5C:  # quoted-pair
            index += 1
            if index >= len(data):
                raise MalformedFramingError("Incomplete quoted chunk extension")
            escaped = data[index]
            if not (escaped == 0x09 or 0x20 <= escaped <= 0x7E or escaped >= 0x80):
                raise MalformedFramingError("Control character in chunk extension")
        elif not (
            value == 0x09
            or value == 0x20
            or value == 0x21
            or 0x23 <= value <= 0x5B
            or 0x5D <= value <= 0x7E
            or value >= 0x80
        ):
            raise MalformedFramingError("Control character in chunk extension")
        index += 1
    raise MalformedFramingError("Unterminated quoted chunk extension")


def _validate_chunk_extensions(data: bytes, index: int) -> None:
    """Validate the chunk-ext grammar, including rejecting illegal controls."""
    while True:
        index = _skip_ows(data, index)
        if index == len(data):
            return
        if data[index] != ord(";"):
            raise MalformedFramingError("Invalid chunk extension syntax")
        index = _skip_ows(data, index + 1)

        name_start = index
        while index < len(data) and _is_token_char(data[index]):
            index += 1
        if index == name_start:
            raise MalformedFramingError("Chunk extension name must be a token")

        after_name = index
        index = _skip_ows(data, index)
        if index < len(data) and data[index] == ord("="):
            index = _skip_ows(data, index + 1)
            if index == len(data):
                raise MalformedFramingError("Chunk extension value is missing")
            if data[index] == ord('"'):
                index = _parse_quoted_extension(data, index)
            else:
                value_start = index
                while index < len(data) and _is_token_char(data[index]):
                    index += 1
                if index == value_start:
                    raise MalformedFramingError("Invalid chunk extension value")
        elif index == len(data) and index != after_name:
            raise MalformedFramingError("Invalid trailing whitespace in chunk extension")

        after_value = index
        index = _skip_ows(data, index)
        if index == len(data) and index != after_value:
            raise MalformedFramingError("Invalid trailing whitespace in chunk extension")
        if index < len(data) and data[index] != ord(";"):
            raise MalformedFramingError("Invalid chunk extension syntax")


def _parse_chunk_header(header: bytes, max_chunk_size: int) -> int:
    """Parse a chunk-size line and return its bounded hexadecimal size."""
    index = 0
    while index < len(header) and header[index] in _HEX_DIGITS:
        index += 1
    if index == 0:
        raise MalformedFramingError("Chunk size must contain hexadecimal digits")

    suffix = header[index:]
    if suffix:
        extension_start = _skip_ows(suffix, 0)
        if extension_start == len(suffix) or suffix[extension_start] != ord(";"):
            raise MalformedFramingError("Invalid data after chunk size")
        _validate_chunk_extensions(suffix, extension_start)

    # Accumulate with an early bound rather than int(..., 16), which can reject
    # otherwise valid long headers due to Python's integer-string digit limit.
    chunk_size = 0
    for digit in header[:index]:
        if 48 <= digit <= 57:
            value = digit - 48
        elif 65 <= digit <= 70:
            value = digit - 55
        else:
            value = digit - 87
        chunk_size = chunk_size * 16 + value
        if chunk_size > max_chunk_size:
            raise SizeLimitExceededError(
                f"Chunk size exceeds maximum {max_chunk_size}"
            )
    return chunk_size


class ChunkedDecoder:
    """Decode an HTTP chunked body from arbitrarily fragmented byte streams."""

    _SIZE = "size"
    _DATA = "data"
    _DATA_CRLF = "data_crlf"
    _TRAILERS = "trailers"
    _DONE = "done"
    _ERROR = "error"

    def __init__(
        self,
        max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
        max_header_bytes: int = DEFAULT_MAX_HEADER_BYTES,
        max_trailer_bytes: int = DEFAULT_MAX_TRAILER_BYTES,
    ):
        self.max_chunk_size = max_chunk_size
        self.max_header_bytes = max_header_bytes
        self.max_trailer_bytes = max_trailer_bytes
        self._state = self._SIZE
        self._done = False
        self._error = None
        self._buf = bytearray()
        self._chunk_remaining = 0
        self._trailer_buf = bytearray()
        self._trailers: Dict[bytes, bytes] = {}

    @property
    def done(self) -> bool:
        return self._done

    @property
    def trailers(self) -> Dict[bytes, bytes]:
        """Parsed trailer fields, keyed by lower-case field name bytes."""
        return self._trailers

    def _fail(self, error: ChunkedDecodeError) -> None:
        self._error = error
        self._state = self._ERROR
        raise error

    @staticmethod
    def _check_partial_line(data: bytearray, kind: str) -> None:
        """Reject bare line breaks and illegal controls, retaining split CRLF."""
        if b"\n" in data:
            raise MalformedFramingError(f"Bare LF in {kind}")
        cr_index = data.find(b"\r")
        if cr_index != -1 and cr_index != len(data) - 1:
            raise MalformedFramingError(f"Bare CR in {kind}")
        for index, value in enumerate(data):
            if value == 0x0D and index == len(data) - 1:
                continue
            if (value < 0x20 and value != 0x09) or value == 0x7F:
                raise MalformedFramingError(f"Control character in {kind}")

    def feed(self, data: bytes) -> bytes:
        if self._state == self._ERROR:
            raise ChunkedDecodeError("Decoder is in an unrecoverable error state") from self._error
        if self._done:
            self._fail(TrailingDataError("Data fed after the chunked message completed"))
        if not isinstance(data, (bytes, bytearray, memoryview)):
            error = TypeError("feed() requires bytes, bytearray, or memoryview")
            self._error = error
            self._state = self._ERROR
            raise error

        self._buf.extend(bytes(data))
        output = bytearray()
        try:
            while True:
                if self._state == self._SIZE:
                    crlf = self._buf.find(b"\r\n")
                    if crlf < 0:
                        self._check_partial_line(self._buf, "chunk header")
                        partial_length = len(self._buf)
                        if self._buf.endswith(b"\r"):
                            partial_length -= 1
                        if partial_length > self.max_header_bytes:
                            raise SizeLimitExceededError(
                                f"Chunk header exceeds maximum {self.max_header_bytes} bytes"
                            )
                        break
                    if crlf > self.max_header_bytes:
                        raise SizeLimitExceededError(
                            f"Chunk header exceeds maximum {self.max_header_bytes} bytes"
                        )
                    header = bytes(self._buf[:crlf])
                    del self._buf[: crlf + 2]
                    chunk_size = _parse_chunk_header(header, self.max_chunk_size)
                    if chunk_size == 0:
                        self._state = self._TRAILERS
                    else:
                        self._chunk_remaining = chunk_size
                        self._state = self._DATA

                elif self._state == self._DATA:
                    if not self._buf:
                        break
                    take = min(self._chunk_remaining, len(self._buf))
                    output.extend(self._buf[:take])
                    del self._buf[:take]
                    self._chunk_remaining -= take
                    if self._chunk_remaining == 0:
                        self._state = self._DATA_CRLF

                elif self._state == self._DATA_CRLF:
                    if not self._buf:
                        break
                    if self._buf[0] != 0x0D:
                        raise MalformedFramingError("Chunk data is not followed by CRLF")
                    if len(self._buf) < 2:
                        break
                    if self._buf[1] != 0x0A:
                        raise MalformedFramingError("Chunk data is not followed by CRLF")
                    del self._buf[:2]
                    self._state = self._SIZE

                elif self._state == self._TRAILERS:
                    crlf = self._buf.find(b"\r\n")
                    if crlf < 0:
                        self._check_partial_line(self._buf, "trailer section")
                        partial_length = len(self._buf)
                        if self._buf == b"\r":
                            partial_length = 0  # possible final empty-line CRLF
                        elif self._buf.endswith(b"\r"):
                            partial_length -= 1
                        if partial_length > MAX_TRAILER_LINE_LENGTH:
                            raise SizeLimitExceededError(
                                "Trailer field line exceeds maximum "
                                f"{MAX_TRAILER_LINE_LENGTH} bytes"
                            )
                        if partial_length and (
                            len(self._trailer_buf) + partial_length + 2
                            > self.max_trailer_bytes
                        ):
                            raise SizeLimitExceededError(
                                f"Trailer section exceeds maximum {self.max_trailer_bytes} bytes"
                            )
                        break

                    if crlf > MAX_TRAILER_LINE_LENGTH:
                        raise SizeLimitExceededError(
                            "Trailer field line exceeds maximum "
                            f"{MAX_TRAILER_LINE_LENGTH} bytes"
                        )
                    line = bytes(self._buf[:crlf])
                    del self._buf[: crlf + 2]
                    if not line:
                        self._trailers = parse_trailers_section(
                            bytes(self._trailer_buf), self.max_trailer_bytes
                        )
                        self._done = True
                        self._state = self._DONE
                        if self._buf:
                            raise TrailingDataError(
                                "Unexpected data follows the chunked message"
                            )
                        break

                    self._trailer_buf.extend(line)
                    self._trailer_buf.extend(b"\r\n")
                    if len(self._trailer_buf) > self.max_trailer_bytes:
                        raise SizeLimitExceededError(
                            f"Trailer section exceeds maximum {self.max_trailer_bytes} bytes"
                        )

                elif self._state == self._DONE:
                    if self._buf:
                        raise TrailingDataError(
                            "Unexpected data follows the chunked message"
                        )
                    break

                else:  # Defensive guard against accidental invalid internal state.
                    raise ChunkedDecodeError("Invalid decoder state")

        except ChunkedDecodeError as error:
            self._error = error
            self._state = self._ERROR
            raise

        return bytes(output)

    def finish(self) -> None:
        if self._state == self._ERROR:
            raise ChunkedDecodeError("Decoder is in an unrecoverable error state") from self._error
        if self._done:
            return
        self._fail(IncompleteMessageError("Chunked message ended before completion"))
