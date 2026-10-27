# Task 5: Streaming HTTP Chunked Decoder

This directory contains the fixture for Task 5: Streaming HTTP Chunked Decoder.

## Repository Overview
- `src/chunked_decoder.py`: Streaming chunked decoder implementation. **This is the ONLY file you are permitted to modify.**
- `src/constants.py`: Default size limits and configuration constants.
- `src/exceptions.py`: Standard exception hierarchy (`ChunkedDecodeError`, `MalformedFramingError`, `SizeLimitExceededError`, `IncompleteMessageError`, `TrailingDataError`).
- `src/headers.py`: Trailer header parsing and validation helper utilities.
- `tests/`: Automated unit and fragmentation test suites.

## Running Tests
Run public tests using:
```bash
python3 -m unittest discover -s tests
```
