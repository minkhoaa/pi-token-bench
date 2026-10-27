"""Tests for size limits and chunk extensions."""

import unittest
from src.chunked_decoder import ChunkedDecoder
from src.exceptions import SizeLimitExceededError


class TestLimitsAndExtensions(unittest.TestCase):
    def test_chunk_extensions_accepted(self):
        decoder = ChunkedDecoder()
        stream = b"4;name=val;ext\r\ntest\r\n0\r\n\r\n"
        out = decoder.feed(stream)
        decoder.finish()
        self.assertTrue(decoder.done)
        self.assertEqual(out, b"test")

    def test_max_chunk_size_exceeded(self):
        decoder = ChunkedDecoder(max_chunk_size=10)
        # Header specifies 15 (0xf) bytes, exceeding max_chunk_size of 10
        stream = b"f\r\n0123456789abcde\r\n0\r\n\r\n"
        with self.assertRaises(SizeLimitExceededError):
            decoder.feed(stream)

    def test_max_header_bytes_exceeded(self):
        decoder = ChunkedDecoder(max_header_bytes=10)
        # Long chunk header with extension exceeding 10 bytes before CRLF
        stream = b"1;verylongextension=value\r\nx\r\n0\r\n\r\n"
        with self.assertRaises(SizeLimitExceededError):
            decoder.feed(stream)

    def test_max_trailer_bytes_exceeded(self):
        decoder = ChunkedDecoder(max_trailer_bytes=20)
        # Trailer section exceeding 20 bytes
        stream = b"0\r\nX-Custom-Header: This is a very long trailer value that exceeds twenty bytes\r\n\r\n"
        with self.assertRaises(SizeLimitExceededError):
            decoder.feed(stream)


if __name__ == "__main__":
    unittest.main()
