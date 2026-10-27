"""Basic public tests for ChunkedDecoder."""

import unittest
from src.chunked_decoder import ChunkedDecoder
from src.exceptions import (
    IncompleteMessageError,
    MalformedFramingError,
    TrailingDataError,
)


class TestBasicChunkedDecoder(unittest.TestCase):
    def test_single_chunk(self):
        decoder = ChunkedDecoder()
        payload = b"Hello, World!"
        stream = f"{len(payload):x}\r\n".encode("ascii") + payload + b"\r\n0\r\n\r\n"
        out = decoder.feed(stream)
        decoder.finish()
        self.assertTrue(decoder.done)
        self.assertEqual(out, payload)

    def test_multi_chunk(self):
        decoder = ChunkedDecoder()
        stream = b"4\r\nWiki\r\n6\r\npedia \r\n0\r\n\r\n"
        out = decoder.feed(stream)
        decoder.finish()
        self.assertTrue(decoder.done)
        self.assertEqual(out, b"Wikipedia ")

    def test_empty_message(self):
        decoder = ChunkedDecoder()
        stream = b"0\r\n\r\n"
        out = decoder.feed(stream)
        decoder.finish()
        self.assertTrue(decoder.done)
        self.assertEqual(out, b"")

    def test_finish_on_incomplete_raises(self):
        decoder = ChunkedDecoder()
        decoder.feed(b"5\r\nHello")
        self.assertFalse(decoder.done)
        with self.assertRaises(IncompleteMessageError):
            decoder.finish()

    def test_trailing_data_raises(self):
        decoder = ChunkedDecoder()
        decoder.feed(b"0\r\n\r\n")
        self.assertTrue(decoder.done)
        with self.assertRaises(TrailingDataError):
            decoder.feed(b"extra")

    def test_finish_idempotent_when_done(self):
        decoder = ChunkedDecoder()
        decoder.feed(b"0\r\n\r\n")
        self.assertTrue(decoder.done)
        decoder.finish()
        decoder.finish()
        self.assertTrue(decoder.done)


if __name__ == "__main__":
    unittest.main()
