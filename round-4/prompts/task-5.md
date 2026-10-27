# Task 5: Streaming HTTP Chunked Decoder Repair

You are tasked with diagnosing and repairing an incremental HTTP/1.1 chunked transfer decoder in the current working directory.

## Repository Overview
Inspect the existing repository structure:
- `src/chunked_decoder.py`: Core streaming decoder implementation containing the `ChunkedDecoder` class. **This is the ONLY file you are permitted to modify.**
- `src/constants.py`: Default size limits and configuration constants (`DEFAULT_MAX_CHUNK_SIZE`, `DEFAULT_MAX_HEADER_BYTES`, `DEFAULT_MAX_TRAILER_BYTES`).
- `src/exceptions.py`: Custom exception hierarchy (`ChunkedDecodeError`, `MalformedFramingError`, `SizeLimitExceededError`, `IncompleteMessageError`, `TrailingDataError`).
- `src/headers.py`: Helper functions for parsing and validating trailer fields per RFC 9112 Section 7.1.2.
- `tests/`: Automated unit and fragmentation test suites.

## Objectives & Requirements
The current implementation in `src/chunked_decoder.py` is naive and defective. It assumes messages arrive all at once, crashes on fragmented input, fails on chunk extensions, fails to enforce size limits, and improperly handles end-of-stream transitions.

Repair `src/chunked_decoder.py` so that it satisfies all of the following requirements:

1. **Chunked Framing & Streaming State Machine (RFC 9112 Section 7.1):**
   - Correctly decode streams partitioned into chunk-size headers (`<hex-size>[;extensions]\r\n`), chunk-data, and chunk-terminating `\r\n`.
   - Support incremental streaming via `feed(data: bytes) -> bytes`. Slices of input can be delivered in arbitrary byte fragmentations, including byte-by-byte or split across CRLF delimiters.
   - Return payload bytes incrementally as soon as they are decoded from chunk data.
   - Payload data may contain arbitrary binary sequences, including null bytes (`\x00`) and literal `\r\n` bytes.
   - Recognize hex chunk sizes case-insensitively, allowing leading zeros (e.g. `000a`, `1F`, `0`).

2. **Chunk Extensions:**
   - Properly parse chunk headers containing extensions following a semicolon `;` (e.g. `4;ext=val\r\n`).
   - Extensions must not cause hexadecimal conversion errors.
   - Reject extensions containing illegal control characters with `MalformedFramingError`.

3. **Trailers Section:**
   - Detect the terminating zero-length chunk (`0\r\n`).
   - Parse subsequent trailer lines until the terminating empty line `\r\n` is reached. Utilize the helper functions in `src/headers.py` (e.g. `parse_trailers_section`) to parse and validate trailer headers.
   - Expose parsed trailers through the `trailers` property on `ChunkedDecoder`.
   - Reject disallowed trailer fields (such as `Transfer-Encoding`, `Content-Length`) or malformed trailer syntax with `MalformedFramingError`.

4. **Limits & Protection:**
   - Enforce configurable limits supplied at initialization:
     - `max_chunk_size` (default 8 MB): If a chunk header declares a size larger than `max_chunk_size`, raise `SizeLimitExceededError`.
     - `max_header_bytes` (default 8 KB): If a chunk header line exceeds this limit before CRLF is reached, raise `SizeLimitExceededError`.
     - `max_trailer_bytes` (default 16 KB): If the trailer section exceeds this limit, raise `SizeLimitExceededError`.

5. **Lifecycle, Completion, and Error State:**
   - `feed(data)` must accept `bytes`, `bytearray`, or `memoryview`. Passing any other type must raise `TypeError`.
   - The `@property done` must return `True` only after the zero-length chunk and the terminating CRLF of the trailer section have been completely processed.
   - `finish()` signals the clean end of input:
     - If called when `done` is `True`, `finish()` must be idempotent and succeed without error.
     - If called when the message is incomplete (e.g. midway through chunk data, or before terminating chunk/trailers), `finish()` must raise `IncompleteMessageError`.
   - Trailing data rejection: If `feed()` is called after `done` is `True`, or if unexpected data follows the terminating CRLF in the stream, raise `TrailingDataError`.
   - Once an error is encountered, the decoder must transition to an unrecoverable error state where subsequent calls to `feed()` or `finish()` raise a `ChunkedDecodeError`.

## Strict Constraints
- **Modify ONLY `src/chunked_decoder.py`**. Do not modify tests, fixtures, `src/constants.py`, `src/exceptions.py`, or `src/headers.py`.
- Python standard library only.
- Do not install external packages or make network calls.
- Do not disable, delete, or weaken existing tests in `tests/`.

## Verification
- Run existing test suites: `python3 -m unittest discover -s tests`.
- When finished, ensure all tests pass cleanly.
