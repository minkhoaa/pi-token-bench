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
    """Decode a chunked message as input bytes arrive."""

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
        self._buf = bytearray()
        self._chunk_remaining = 0
        self._trailers: Dict[bytes, bytes] = {}
        self._failed = False
        self._done = False

    @property
    def done(self) -> bool:
        """Whether the terminating trailer-section CRLF has been processed."""
        return self._done

    @property
    def trailers(self) -> Dict[bytes, bytes]:
        """Parsed trailer fields, keyed by lower-case field name bytes."""
        return self._trailers

    def feed(self, data: bytes) -> bytes:
        """Consume one fragment of the encoded message and return decoded data."""
        self._check_failed()
        if self._done:
            self._raise(TrailingDataError("Data fed after the chunked message completed"))
        if not isinstance(data, (bytes, bytearray, memoryview)):
            self._failed = True
            raise TypeError("feed() requires bytes, bytearray, or memoryview")

        try:
            self._buf.extend(bytes(data))
            output = bytearray()

            while True:
                if self._state == self._SIZE:
                    if not self._read_chunk_header():
                        break
                elif self._state == self._DATA:
                    if not self._read_chunk_data(output):
                        break
                elif self._state == self._DATA_CRLF:
                    if not self._read_chunk_terminator():
                        break
                elif self._state == self._TRAILERS:
                    if not self._read_trailers():
                        break
                else:  # DONE
                    if self._buf:
                        raise TrailingDataError(
                            "Unexpected data after the chunked message completed"
                        )
                    break

            return bytes(output)
        except ChunkedDecodeError:
            self._failed = True
            raise

    def finish(self) -> None:
        """Signal end of input, raising if the chunked message is incomplete."""
        self._check_failed()
        if self._done:
            return
        self._raise(IncompleteMessageError("Chunked message ended before completion"))

    def _check_failed(self) -> None:
        if self._failed:
            raise ChunkedDecodeError("Decoder is in an unrecoverable error state")

    def _raise(self, error: ChunkedDecodeError):
        self._failed = True
        raise error

    @staticmethod
    def _is_token_char(value: int) -> bool:
        return (
            48 <= value <= 57
            or 65 <= value <= 90
            or 97 <= value <= 122
            or value in b"!#$%&'*+-.^_`|~"
        )

    @staticmethod
    def _check_header_controls(line: bytes) -> None:
        for value in line:
            if (value < 0x20 and value != 0x09) or value == 0x7F:
                raise MalformedFramingError(
                    f"Illegal control character in chunk header: {value:#x}"
                )

    @classmethod
    def _parse_extensions(cls, extensions: bytes) -> None:
        """Validate the RFC chunk-ext syntax, including quoted values."""
        length = len(extensions)
        pos = 0

        def skip_bws(index: int) -> int:
            while index < length and extensions[index] in (0x20, 0x09):
                index += 1
            return index

        pos = skip_bws(pos)
        while pos < length:
            if extensions[pos] != ord(";"):
                raise MalformedFramingError("Malformed chunk extension")
            pos = skip_bws(pos + 1)

            name_start = pos
            while pos < length and cls._is_token_char(extensions[pos]):
                pos += 1
            if pos == name_start:
                raise MalformedFramingError("Missing chunk extension name")

            pos = skip_bws(pos)
            if pos < length and extensions[pos] == ord("="):
                pos = skip_bws(pos + 1)
                if pos >= length:
                    raise MalformedFramingError("Missing chunk extension value")

                if extensions[pos] == ord('"'):
                    pos += 1
                    closed = False
                    while pos < length:
                        value = extensions[pos]
                        if value == ord('"'):
                            pos += 1
                            closed = True
                            break
                        if value == ord("\\"):
                            pos += 1
                            if pos >= length:
                                raise MalformedFramingError(
                                    "Incomplete quoted-pair in chunk extension"
                                )
                            escaped = extensions[pos]
                            if not (
                                escaped == 0x09
                                or 0x20 <= escaped <= 0x7E
                                or escaped >= 0x80
                            ):
                                raise MalformedFramingError(
                                    "Illegal control character in chunk extension"
                                )
                            pos += 1
                            continue
                        if not (
                            value in (0x09, 0x20)
                            or value == 0x21
                            or 0x23 <= value <= 0x5B
                            or 0x5D <= value <= 0x7E
                            or value >= 0x80
                        ):
                            raise MalformedFramingError(
                                "Illegal character in quoted chunk extension"
                            )
                        pos += 1
                    if not closed:
                        raise MalformedFramingError(
                            "Unterminated quoted chunk extension"
                        )
                else:
                    value_start = pos
                    while pos < length and cls._is_token_char(extensions[pos]):
                        pos += 1
                    if pos == value_start:
                        raise MalformedFramingError("Invalid chunk extension value")

                pos = skip_bws(pos)

            if pos < length and extensions[pos] != ord(";"):
                raise MalformedFramingError("Malformed chunk extension")

    def _parse_chunk_header(self, line: bytes) -> int:
        self._check_header_controls(line)

        semicolon = line.find(b";")
        if semicolon < 0:
            size_text = line
        else:
            size_text = line[:semicolon].rstrip(b" \t")
            self._parse_extensions(line[semicolon:])

        if not size_text:
            raise MalformedFramingError("Missing chunk size")

        size = 0
        for value in size_text:
            if 48 <= value <= 57:
                digit = value - 48
            elif 65 <= value <= 70:
                digit = value - 65 + 10
            elif 97 <= value <= 102:
                digit = value - 97 + 10
            else:
                raise MalformedFramingError("Invalid hexadecimal chunk size")
            size = size * 16 + digit

        if size > self.max_chunk_size:
            raise SizeLimitExceededError(
                f"Chunk size {size} exceeds maximum {self.max_chunk_size}"
            )
        return size

    def _read_chunk_header(self) -> bool:
        end = self._buf.find(b"\r\n")
        if end < 0:
            # A final CR may be the first half of the delimiter; do not count it
            # as part of the header line until the next fragment arrives.
            partial_length = len(self._buf) - int(self._buf.endswith(b"\r"))
            partial = bytes(self._buf)
            for index, value in enumerate(partial):
                if value == 0x0D and index == len(partial) - 1:
                    continue
                if (value < 0x20 and value != 0x09) or value == 0x7F:
                    raise MalformedFramingError(
                        f"Illegal control character in chunk header: {value:#x}"
                    )
            if partial_length > self.max_header_bytes:
                raise SizeLimitExceededError(
                    f"Chunk header exceeds maximum {self.max_header_bytes} bytes"
                )
            return False

        if end > self.max_header_bytes:
            raise SizeLimitExceededError(
                f"Chunk header size {end} exceeds maximum {self.max_header_bytes}"
            )

        line = bytes(self._buf[:end])
        del self._buf[: end + 2]
        chunk_size = self._parse_chunk_header(line)
        if chunk_size == 0:
            self._state = self._TRAILERS
        else:
            self._chunk_remaining = chunk_size
            self._state = self._DATA
        return True

    def _read_chunk_data(self, output: bytearray) -> bool:
        if not self._buf:
            return False

        amount = min(self._chunk_remaining, len(self._buf))
        output.extend(self._buf[:amount])
        del self._buf[:amount]
        self._chunk_remaining -= amount
        if self._chunk_remaining == 0:
            self._state = self._DATA_CRLF
        return True

    def _read_chunk_terminator(self) -> bool:
        if not self._buf:
            return False
        if self._buf[0] != 0x0D:
            raise MalformedFramingError("Chunk data is not followed by CRLF")
        if len(self._buf) == 1:
            return False
        if self._buf[1] != 0x0A:
            raise MalformedFramingError("Chunk data is not followed by CRLF")

        del self._buf[:2]
        self._state = self._SIZE
        return True

    @staticmethod
    def _check_trailer_controls(data: bytes) -> None:
        for index, value in enumerate(data):
            if value == 0x0D:
                if index + 1 == len(data):
                    continue  # possible first byte of a split CRLF
                if data[index + 1] != 0x0A:
                    raise MalformedFramingError("Bare CR in trailer section")
            elif value == 0x0A:
                if index == 0 or data[index - 1] != 0x0D:
                    raise MalformedFramingError("Bare LF in trailer section")
            elif (value < 0x20 and value != 0x09) or value == 0x7F:
                raise MalformedFramingError(
                    f"Illegal control character in trailer section: {value:#x}"
                )

    def _read_trailers(self) -> bool:
        # The empty trailer section is just its terminating CRLF.
        if self._buf.startswith(b"\r\n"):
            raw_trailers = b""
            consumed = 2
        else:
            end = self._buf.find(b"\r\n\r\n")
            if end < 0:
                buffered = bytes(self._buf)
                self._check_trailer_controls(buffered)
                # The last CR could be the first half of the terminating CRLF.
                possible_size = len(self._buf) - int(self._buf.endswith(b"\r"))
                if possible_size > self.max_trailer_bytes:
                    raise SizeLimitExceededError(
                        f"Trailer section exceeds maximum {self.max_trailer_bytes} bytes"
                    )
                return False

            # Include the last field-line CRLF in the raw block, but exclude
            # the final CRLF that terminates the trailer section.
            raw_trailers = bytes(self._buf[: end + 2])
            consumed = end + 4

        if len(raw_trailers) > self.max_trailer_bytes:
            raise SizeLimitExceededError(
                f"Trailer section size {len(raw_trailers)} exceeds maximum "
                f"{self.max_trailer_bytes}"
            )

        self._trailers = parse_trailers_section(
            raw_trailers, max_trailer_bytes=self.max_trailer_bytes
        )
        del self._buf[:consumed]
        self._state = self._DONE
        self._done = True
        if self._buf:
            raise TrailingDataError(
                "Unexpected data after the chunked message completed"
            )
        return True
