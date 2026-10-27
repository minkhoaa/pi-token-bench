"""Tests for fragmented feed input in ChunkedDecoder."""

import unittest
from src.chunked_decoder import ChunkedDecoder


class TestFragmentation(unittest.TestCase):
    def test_byte_by_byte(self):
        decoder = ChunkedDecoder()
        payload = b"Streaming chunked decoding byte by byte."
        encoded = f"{len(payload):x}\r\n".encode("ascii") + payload + b"\r\n0\r\n\r\n"

        collected = bytearray()
        for i in range(len(encoded)):
            chunk = encoded[i : i + 1]
            collected.extend(decoder.feed(chunk))

        decoder.finish()
        self.assertTrue(decoder.done)
        self.assertEqual(bytes(collected), payload)

    def test_split_crlf(self):
        decoder = ChunkedDecoder()
        # Header CRLF split across feeds
        parts = [
            b"4",
            b"\r",
            b"\n",
            b"test",
            b"\r",
            b"\n",
            b"0\r",
            b"\n\r\n",
        ]
        collected = bytearray()
        for p in parts:
            collected.extend(decoder.feed(p))

        decoder.finish()
        self.assertTrue(decoder.done)
        self.assertEqual(bytes(collected), b"test")

    def test_variable_chunks_and_binary(self):
        decoder = ChunkedDecoder()
        # Binary data containing NUL and CRLF sequences
        data = b"\x00\x01\x02\r\n\x00\r\n\xff"
        encoded = f"{len(data):x}\r\n".encode("ascii") + data + b"\r\n0\r\n\r\n"

        collected = bytearray()
        step = 3
        for i in range(0, len(encoded), step):
            collected.extend(decoder.feed(encoded[i : i + step]))

        decoder.finish()
        self.assertTrue(decoder.done)
        self.assertEqual(bytes(collected), data)


if __name__ == "__main__":
    unittest.main()
