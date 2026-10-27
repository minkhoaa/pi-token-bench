"""Incremental HTTP/1.1 chunked transfer decoder."""

from typing import Dict

from .constants import (
    DEFAULT_MAX_CHUNK_SIZE,
    DEFAULT_MAX_HEADER_BYTES,
    DEFAULT_MAX_TRAILER_BYTES,
)
from .exceptions import (
    ChunkedDecodeError,
    IncompleteMessageError,
    MalformedFramingError,
    SizeLimitExceededError,
    TrailingDataError,
)
from .headers import parse_trailers_section


_TCHAR_PUNCTUATION = b"!#$%&'*+-.^_`|~"


def _is_tchar(value: int) -> bool:
    return (
        48 <= value <= 57
        or 65 <= value <= 90
        or 97 <= value <= 122
        or value in _TCHAR_PUNCTUATION
    )


def _skip_ows(value: bytes, index: int) -> int:
    while index < len(value) and value[index] in (0x20, 0x09):
        index += 1
    return index


def _validate_chunk_extensions(extensions: bytes) -> None:
    """Validate the chunk-ext grammar and reject forbidden control bytes."""
    for value in extensions:
        if (value < 0x20 and value != 0x09) or value == 0x7F:
            raise MalformedFramingError("Control character in chunk extension")

    index = 0
    while index < len(extensions):
        index = _skip_ows(extensions, index)
        if index >= len(extensions) or extensions[index] != ord(";"):
            raise MalformedFramingError("Malformed chunk extension")
        index += 1
        index = _skip_ows(extensions, index)

        name_start = index
        while index < len(extensions) and _is_tchar(extensions[index]):
            index += 1
        if index == name_start:
            raise MalformedFramingError("Missing chunk extension name")

        name_end = index
        after_name = _skip_ows(extensions, index)
        if after_name < len(extensions) and extensions[after_name] == ord("="):
            index = after_name + 1
            index = _skip_ows(extensions, index)
            if index >= len(extensions):
                raise MalformedFramingError("Missing chunk extension value")

            if extensions[index] == ord('"'):
                index += 1
                closed = False
                while index < len(extensions):
                    value = extensions[index]
                    if value == ord('"'):
                        index += 1
                        closed = True
                        break
                    if value == ord("\\"):
                        index += 1
                        if index >= len(extensions):
                            raise MalformedFramingError("Invalid quoted chunk extension")
                        escaped = extensions[index]
                        if not (
                            escaped == 0x09
                            or 0x20 <= escaped <= 0x7E
                            or escaped >= 0x80
                        ):
                            raise MalformedFramingError("Invalid quoted chunk extension")
                        index += 1
                        continue
                    if not (
                        value == 0x09
                        or value == 0x20
                        or value == 0x21
                        or 0x23 <= value <= 0x5B
                        or 0x5D <= value <= 0x7E
                        or value >= 0x80
                    ):
                        raise MalformedFramingError("Invalid quoted chunk extension")
                    index += 1
                if not closed:
                    raise MalformedFramingError("Unterminated quoted chunk extension")
            else:
                value_start = index
                while index < len(extensions) and _is_tchar(extensions[index]):
                    index += 1
                if index == value_start:
                    raise MalformedFramingError("Invalid chunk extension value")

        else:
            index = name_end

        # Any following whitespace belongs before another semicolon. The next
        # iteration rejects it if there is no subsequent extension.


def _parse_chunk_header(header: bytes, max_chunk_size: int) -> int:
    index = 0
    while index < len(header) and (
        48 <= header[index] <= 57
        or 65 <= header[index] <= 70
        or 97 <= header[index] <= 102
    ):
        index += 1

    digits = header[:index]
    if not digits:
        raise MalformedFramingError("Chunk size is missing or is not hexadecimal")

    suffix = header[index:]
    if suffix:
        _validate_chunk_extensions(suffix)

    size = 0
    for digit in digits:
        if 48 <= digit <= 57:
            value = digit - 48
        elif 65 <= digit <= 70:
            value = digit - 55
        else:
            value = digit - 87
        size = size * 16 + value
        if size > max_chunk_size:
            raise SizeLimitExceededError(
                f"Chunk size exceeds maximum {max_chunk_size}"
            )

    return size


class ChunkedDecoder:
    """Decode an HTTP/1.1 chunked body from arbitrarily fragmented input."""

    _SIZE = "size"
    _DATA = "data"
    _DATA_CRLF = "data_crlf"
    _TRAILERS = "trailers"
    _DONE = "done"

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
        self._failed = False
        self._buf = bytearray()
        self._chunk_remaining = 0
        self._trailer_buf = bytearray()
        self._trailers: Dict[bytes, bytes] = {}

    @property
    def done(self) -> bool:
        return self._done

    @property
    def trailers(self) -> Dict[bytes, bytes]:
        return self._trailers

    def feed(self, data: bytes) -> bytes:
        if self._failed:
            raise ChunkedDecodeError("Decoder is in an unrecoverable error state")
        if not isinstance(data, (bytes, bytearray, memoryview)):
            self._failed = True
            raise TypeError("feed() requires bytes, bytearray, or memoryview")
        if self._done:
            self._failed = True
            raise TrailingDataError("Data supplied after the chunked message completed")

        try:
            self._buf.extend(bytes(data))
            return self._process()
        except Exception:
            self._failed = True
            raise

    def _process(self) -> bytes:
        output = bytearray()

        while True:
            if self._state == self._SIZE:
                crlf = self._buf.find(b"\r\n")
                if crlf < 0:
                    partial_length = len(self._buf)
                    if self._buf.endswith(b"\r"):
                        partial_length -= 1
                    if partial_length > self.max_header_bytes:
                        raise SizeLimitExceededError("Chunk header exceeds maximum size")
                    break

                if crlf > self.max_header_bytes:
                    raise SizeLimitExceededError("Chunk header exceeds maximum size")
                header = bytes(self._buf[:crlf])
                del self._buf[: crlf + 2]
                chunk_size = _parse_chunk_header(header, self.max_chunk_size)
                if chunk_size:
                    self._chunk_remaining = chunk_size
                    self._state = self._DATA
                else:
                    self._state = self._TRAILERS

            elif self._state == self._DATA:
                if not self._buf:
                    break
                take = min(self._chunk_remaining, len(self._buf))
                output.extend(self._buf[:take])
                del self._buf[:take]
                self._chunk_remaining -= take
                if self._chunk_remaining:
                    break
                self._state = self._DATA_CRLF

            elif self._state == self._DATA_CRLF:
                if not self._buf:
                    break
                if self._buf[0] != 0x0D:
                    raise MalformedFramingError("Chunk data is not followed by CRLF")
                if len(self._buf) == 1:
                    break
                if self._buf[1] != 0x0A:
                    raise MalformedFramingError("Chunk data is not followed by CRLF")
                del self._buf[:2]
                self._state = self._SIZE

            elif self._state == self._TRAILERS:
                crlf = self._buf.find(b"\r\n")
                if crlf < 0:
                    if b"\n" in self._buf or b"\r" in self._buf[:-1]:
                        raise MalformedFramingError("Malformed trailer line ending")
                    partial_length = len(self._buf)
                    if self._buf.endswith(b"\r"):
                        partial_length -= 1
                    if len(self._trailer_buf) + partial_length > self.max_trailer_bytes:
                        raise SizeLimitExceededError("Trailer section exceeds maximum size")
                    break

                if crlf == 0:
                    del self._buf[:2]
                    self._trailers = parse_trailers_section(
                        bytes(self._trailer_buf), self.max_trailer_bytes
                    )
                    self._state = self._DONE
                    self._done = True
                    if self._buf:
                        raise TrailingDataError(
                            "Data supplied after the chunked message completed"
                        )
                    break

                trailer_size = len(self._trailer_buf) + crlf + 2
                if trailer_size > self.max_trailer_bytes:
                    raise SizeLimitExceededError("Trailer section exceeds maximum size")
                self._trailer_buf.extend(self._buf[:crlf])
                self._trailer_buf.extend(b"\r\n")
                del self._buf[: crlf + 2]

            else:  # completed state
                if self._buf:
                    raise TrailingDataError(
                        "Data supplied after the chunked message completed"
                    )
                break

        return bytes(output)

    def finish(self) -> None:
        if self._failed:
            raise ChunkedDecodeError("Decoder is in an unrecoverable error state")
        if self._done:
            return
        self._failed = True
        raise IncompleteMessageError("Chunked message ended before completion")
