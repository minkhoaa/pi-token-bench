"""Incremental HTTP/1.1 chunked transfer decoder (RFC 9112, section 7.1)."""

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
    """Decode chunked transfer coding incrementally as input arrives."""

    _TCHARS = frozenset(
        b"!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    )

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
        self._remaining = 0
        self._trailer_raw = bytearray()
        self._trailers: Dict[bytes, bytes] = {}
        self._done = False
        self._failed = False

    @property
    def done(self) -> bool:
        """Whether the terminating trailer-section CRLF has been processed."""
        return self._done

    @property
    def trailers(self) -> Dict[bytes, bytes]:
        """Parsed trailer fields, keyed by lower-case field name bytes."""
        return self._trailers

    def feed(self, data: bytes) -> bytes:
        """Consume a fragment and return any payload decoded from it."""
        if self._failed:
            raise ChunkedDecodeError("decoder is in an unrecoverable error state")

        try:
            if not isinstance(data, (bytes, bytearray, memoryview)):
                raise TypeError("feed() requires bytes, bytearray, or memoryview")
            if self._done:
                raise TrailingDataError("data supplied after the chunked message ended")

            self._buf += bytes(data)
            return self._decode_available()
        except Exception:
            self._failed = True
            raise

    def finish(self) -> None:
        """Signal end-of-input, rejecting a message that has not completed."""
        if self._failed:
            raise ChunkedDecodeError("decoder is in an unrecoverable error state")
        if self._done:
            return

        self._failed = True
        raise IncompleteMessageError("input ended before the chunked message completed")

    def _decode_available(self) -> bytes:
        out = bytearray()

        while True:
            if self._state == "size":
                line_end = self._buf.find(b"\r\n")
                if line_end < 0:
                    self._validate_partial_line(self._buf)
                    # A final CR may be the first byte of a split CRLF, so it
                    # does not count toward the chunk-header line limit.
                    partial_length = len(self._buf) - int(self._buf.endswith(b"\r"))
                    if partial_length > self.max_header_bytes:
                        raise SizeLimitExceededError(
                            f"chunk header exceeds maximum {self.max_header_bytes} bytes"
                        )
                    break

                if line_end > self.max_header_bytes:
                    raise SizeLimitExceededError(
                        f"chunk header size {line_end} exceeds maximum {self.max_header_bytes}"
                    )

                header = self._buf[:line_end]
                self._buf = self._buf[line_end + 2 :]
                chunk_size = self._parse_chunk_header(header)
                if chunk_size == 0:
                    self._state = "trailers"
                else:
                    self._remaining = chunk_size
                    self._state = "data"

            elif self._state == "data":
                if not self._buf:
                    break
                count = min(self._remaining, len(self._buf))
                out.extend(self._buf[:count])
                self._buf = self._buf[count:]
                self._remaining -= count
                if self._remaining == 0:
                    self._state = "data_cr"

            elif self._state == "data_cr":
                if not self._buf:
                    break
                if self._buf[0] != 0x0D:
                    raise MalformedFramingError("chunk data is not followed by CRLF")
                self._buf = self._buf[1:]
                self._state = "data_lf"

            elif self._state == "data_lf":
                if not self._buf:
                    break
                if self._buf[0] != 0x0A:
                    raise MalformedFramingError("chunk data is not followed by CRLF")
                self._buf = self._buf[1:]
                self._state = "size"

            elif self._state == "trailers":
                line_end = self._buf.find(b"\r\n")
                if line_end < 0:
                    self._validate_partial_line(self._buf)
                    if len(self._trailer_raw) + len(self._buf) > self.max_trailer_bytes:
                        raise SizeLimitExceededError(
                            f"trailer section exceeds maximum {self.max_trailer_bytes} bytes"
                        )
                    break

                section_size = len(self._trailer_raw) + line_end + 2
                if section_size > self.max_trailer_bytes:
                    raise SizeLimitExceededError(
                        f"trailer section size {section_size} exceeds maximum "
                        f"{self.max_trailer_bytes}"
                    )

                line = self._buf[:line_end]
                self._buf = self._buf[line_end + 2 :]
                if not line:
                    self._trailers = parse_trailers_section(
                        bytes(self._trailer_raw), self.max_trailer_bytes
                    )
                    self._done = True
                    self._state = "done"
                    if self._buf:
                        raise TrailingDataError("data follows the terminating trailer CRLF")
                    break

                # Keep the line delimiter: parse_trailers_section expects the
                # raw trailer block without only its final, empty-line CRLF.
                self._trailer_raw.extend(line)
                self._trailer_raw.extend(b"\r\n")

            else:  # completed state; normally handled before entering decoder
                if self._buf:
                    raise TrailingDataError("data follows the terminating trailer CRLF")
                break

        return bytes(out)

    def _parse_chunk_header(self, header: bytes) -> int:
        """Parse the hexadecimal size and optional RFC chunk extensions."""
        self._validate_partial_line(header)

        digit_count = 0
        chunk_size = 0
        for byte in header:
            if 0x30 <= byte <= 0x39:
                digit = byte - 0x30
            elif 0x41 <= byte <= 0x46:
                digit = byte - 0x41 + 10
            elif 0x61 <= byte <= 0x66:
                digit = byte - 0x61 + 10
            else:
                break
            digit_count += 1
            chunk_size = chunk_size * 16 + digit
            if chunk_size > self.max_chunk_size:
                raise SizeLimitExceededError(
                    f"chunk size exceeds maximum {self.max_chunk_size}"
                )

        if digit_count == 0:
            raise MalformedFramingError("chunk header must begin with a hexadecimal size")

        if digit_count < len(header):
            self._parse_chunk_extensions(header[digit_count:])

        return chunk_size

    @classmethod
    def _parse_chunk_extensions(cls, extension: bytes) -> None:
        """Validate chunk extensions using the RFC token/quoted-string grammar."""
        index = 0
        length = len(extension)

        while index < length:
            while index < length and extension[index] in (0x20, 0x09):
                index += 1
            if index >= length or extension[index] != ord(";"):
                raise MalformedFramingError("malformed chunk extension")

            index += 1
            while index < length and extension[index] in (0x20, 0x09):
                index += 1

            name_start = index
            while index < length and extension[index] in cls._TCHARS:
                index += 1
            if index == name_start:
                raise MalformedFramingError("chunk extension is missing a name")

            while index < length and extension[index] in (0x20, 0x09):
                index += 1

            if index < length and extension[index] == ord("="):
                index += 1
                while index < length and extension[index] in (0x20, 0x09):
                    index += 1
                if index >= length:
                    raise MalformedFramingError("chunk extension is missing a value")

                if extension[index] == ord('"'):
                    index += 1
                    closed = False
                    while index < length:
                        byte = extension[index]
                        if byte == ord('"'):
                            index += 1
                            closed = True
                            break
                        if byte == ord("\\"):
                            index += 1
                            if index >= length or not cls._is_quoted_pair_byte(extension[index]):
                                raise MalformedFramingError("invalid quoted chunk extension value")
                            index += 1
                            continue
                        if not cls._is_quoted_text_byte(byte):
                            raise MalformedFramingError(
                                "illegal control character in chunk extension"
                            )
                        index += 1
                    if not closed:
                        raise MalformedFramingError("unterminated quoted chunk extension value")
                else:
                    value_start = index
                    while index < length and extension[index] in cls._TCHARS:
                        index += 1
                    if index == value_start:
                        raise MalformedFramingError("invalid chunk extension value")

            # A following extension may be separated by BWS. The next loop
            # requires its semicolon, so trailing whitespace is rejected.
            if index < length:
                if extension[index] not in (0x20, 0x09, ord(";")):
                    raise MalformedFramingError("malformed chunk extension")

    @staticmethod
    def _is_quoted_text_byte(byte: int) -> bool:
        return (
            byte == 0x09
            or byte == 0x20
            or byte == 0x21
            or 0x23 <= byte <= 0x5B
            or 0x5D <= byte <= 0x7E
            or byte >= 0x80
        )

    @staticmethod
    def _is_quoted_pair_byte(byte: int) -> bool:
        return byte == 0x09 or byte == 0x20 or 0x21 <= byte <= 0x7E or byte >= 0x80

    @staticmethod
    def _validate_partial_line(line: bytes) -> None:
        """Reject bare line breaks and control bytes while allowing split CRLF."""
        last = len(line) - 1
        for index, byte in enumerate(line):
            if byte == 0x0A:
                # Any CRLF would already have been consumed as a line delimiter.
                raise MalformedFramingError("bare LF in chunk framing")
            if byte == 0x0D:
                if index != last:
                    raise MalformedFramingError("bare CR in chunk framing")
            elif (byte < 0x20 and byte != 0x09) or byte == 0x7F:
                raise MalformedFramingError("illegal control character in chunk framing")
