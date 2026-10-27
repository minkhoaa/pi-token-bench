"""Defective starting implementation of ChunkedDecoder.

BUGS AND SHORTCOMINGS:
- Assumes entire chunks arrive all at once (breaks on fragmented feed).
- Fails on chunk extensions (int(line, 16) throws ValueError on ';').
- Does not enforce size limits (max_chunk_size, max_header_bytes, max_trailer_bytes).
- Does not check trailing data after done (silently ignores or crashes).
- Does not validate chunk CRLF framing.
- Does not raise TypeError on non-bytes input.
- Does not handle split CRLF.
"""

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


class ChunkedDecoder:
    """A naive, defective implementation of a chunked decoder."""

    def __init__(
        self,
        max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
        max_header_bytes: int = DEFAULT_MAX_HEADER_BYTES,
        max_trailer_bytes: int = DEFAULT_MAX_TRAILER_BYTES,
    ):
        self.max_chunk_size = max_chunk_size
        self.max_header_bytes = max_header_bytes
        self.max_trailer_bytes = max_trailer_bytes
        self._done = False
        self._buf = b""

    @property
    def done(self) -> bool:
        return self._done

    def feed(self, data: bytes) -> bytes:
        # Naive implementation that doesn't validate types
        self._buf += data
        out = bytearray()

        while self._buf:
            crlf_pos = self._buf.find(b"\r\n")
            if crlf_pos == -1:
                break

            header = self._buf[:crlf_pos]
            try:
                # Bug: fails on chunk extensions e.g. "4;ext=1"
                chunk_len = int(header, 16)
            except ValueError:
                # Bug: does not raise MalformedFramingError properly
                break

            if chunk_len == 0:
                # Naive: assumes immediate \r\n\r\n terminates
                if self._buf.endswith(b"\r\n\r\n"):
                    self._done = True
                self._buf = b""
                break

            start = crlf_pos + 2
            end = start + chunk_len
            if len(self._buf) < end + 2:
                # Not enough data yet
                break

            chunk_data = self._buf[start:end]
            out.extend(chunk_data)
            # Advance buffer past chunk and its trailing CRLF
            self._buf = self._buf[end + 2 :]

        return bytes(out)

    def finish(self) -> None:
        if not self._done:
            # Bug: fails to raise IncompleteMessageError when buf has leftover
            pass
