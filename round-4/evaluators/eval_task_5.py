#!/usr/bin/env python3
"""
Independent external evaluator for Task 5: Streaming HTTP Chunked Decoder.
Evaluates single-file modification constraint (src/chunked_decoder.py only),
public test discovery, and 24 hidden evaluation checks.
"""

import sys
sys.dont_write_bytecode = True

import hashlib
import json
import os
import subprocess
from pathlib import Path


def compute_file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def evaluate_task_5(workspace_dir: str, fixture_dir: str) -> dict:
    ws_path = Path(workspace_dir).resolve()
    fix_path = Path(fixture_dir).resolve()

    target_file = ws_path / "src" / "chunked_decoder.py"

    result = {
        "task": "task-5",
        "workspace_dir": str(ws_path),
        "target_file": "src/chunked_decoder.py",
        "target_exists": False,
        "single_file_modified": False,
        "unexpected_modifications": [],
        "line_count": 0,
        "sha256": None,
        "public_tests_passed": False,
        "public_tests_output": "",
        "hidden_tests_passed": False,
        "hidden_total": 0,
        "hidden_passed": 0,
        "failures": [],
        "status": "FAIL",
    }

    if not target_file.is_file():
        result["failures"].append("Target file 'src/chunked_decoder.py' does not exist")
        return result

    result["target_exists"] = True
    content = target_file.read_bytes()
    result["line_count"] = len(content.splitlines())
    result["sha256"] = hashlib.sha256(content).hexdigest()

    # 1. Verify single file modification rule:
    # All files other than src/chunked_decoder.py must match the fixture exactly.
    fix_files = {p.relative_to(fix_path).as_posix(): p for p in fix_path.rglob("*") if p.is_file()}
    ws_files = {p.relative_to(ws_path).as_posix(): p for p in ws_path.rglob("*") if p.is_file()}

    # Ignore .git or __pycache__ if any
    clean_ws = {k: v for k, v in ws_files.items() if not k.startswith(".git") and "__pycache__" not in k}
    clean_fix = {k: v for k, v in fix_files.items() if not k.startswith(".git") and "__pycache__" not in k}

    unexpected = []
    for rel_path, f_path in clean_fix.items():
        if rel_path == "src/chunked_decoder.py":
            continue
        if rel_path not in clean_ws:
            unexpected.append(f"Deleted required file: {rel_path}")
        else:
            if compute_file_hash(f_path) != compute_file_hash(clean_ws[rel_path]):
                unexpected.append(f"Modified unauthorized file: {rel_path}")

    for rel_path in clean_ws:
        if rel_path != "src/chunked_decoder.py" and rel_path not in clean_fix:
            unexpected.append(f"Added unauthorized file: {rel_path}")

    if unexpected:
        result["unexpected_modifications"] = unexpected
        result["failures"].extend(unexpected)
    else:
        result["single_file_modified"] = True

    # 2. Run Public Tests
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(ws_path)

    try:
        proc = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"],
            cwd=str(ws_path),
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        combined_pub = (proc.stdout + "\n" + proc.stderr).strip()
        result["public_tests_output"] = combined_pub
        if proc.returncode == 0:
            result["public_tests_passed"] = True
        else:
            result["failures"].append(f"Public test discovery failed with exit code {proc.returncode}")
    except subprocess.TimeoutExpired:
        result["failures"].append("Public tests timed out after 30 seconds")
    except Exception as e:
        result["failures"].append(f"Public tests execution error: {e}")

    # 3. Dynamic import of workspace modules for hidden evaluation
    if str(ws_path) in sys.path:
        sys.path.remove(str(ws_path))
    sys.path.insert(0, str(ws_path))

    for mod in list(sys.modules.keys()):
        if mod.startswith("src.") or mod == "src":
            del sys.modules[mod]

    try:
        import src.chunked_decoder as cd_mod
        import src.exceptions as exc_mod
    except Exception as e:
        result["failures"].append(f"Import of workspace modules failed: {e}")
        return result

    ChunkedDecoder = cd_mod.ChunkedDecoder
    ChunkedDecodeError = exc_mod.ChunkedDecodeError
    MalformedFramingError = exc_mod.MalformedFramingError
    SizeLimitExceededError = exc_mod.SizeLimitExceededError
    IncompleteMessageError = exc_mod.IncompleteMessageError
    TrailingDataError = exc_mod.TrailingDataError

    def run_check(name: str, fn):
        result["hidden_total"] += 1
        try:
            fn()
            result["hidden_passed"] += 1
        except Exception as e:
            result["failures"].append(f"Hidden check '{name}' failed: {e}")

    # Check 1: Single basic chunk
    def check_single_chunk():
        dec = ChunkedDecoder()
        data = dec.feed(b"5\r\nHello\r\n0\r\n\r\n")
        dec.finish()
        assert data == b"Hello", f"Expected b'Hello', got {data!r}"
        assert dec.done is True
    run_check("single_chunk", check_single_chunk)

    # Check 2: Multi chunk stream
    def check_multi_chunk():
        dec = ChunkedDecoder()
        res = bytearray()
        res += dec.feed(b"7\r\nMozilla\r\n9\r\nDeveloper\r\n7\r\nNetwork\r\n0\r\n\r\n")
        dec.finish()
        assert bytes(res) == b"MozillaDeveloperNetwork"
        assert dec.done is True
    run_check("multi_chunk", check_multi_chunk)

    # Check 3: Byte-by-byte streaming
    def check_byte_by_byte():
        dec = ChunkedDecoder()
        payload = b"Streaming small slices."
        encoded = f"{len(payload):x}\r\n".encode("ascii") + payload + b"\r\n0\r\n\r\n"
        collected = bytearray()
        for b in encoded:
            collected += dec.feed(bytes([b]))
        dec.finish()
        assert bytes(collected) == payload
        assert dec.done is True
    run_check("byte_by_byte", check_byte_by_byte)

    # Check 4: Payload containing NUL bytes, CR, LF
    def check_binary_with_control_chars():
        dec = ChunkedDecoder()
        payload = b"\x00\r\n\x00\xff\xfe\r\n\x01"
        encoded = f"{len(payload):x}\r\n".encode("ascii") + payload + b"\r\n0\r\n\r\n"
        out = dec.feed(encoded)
        dec.finish()
        assert out == payload
        assert dec.done is True
    run_check("binary_with_control_chars", check_binary_with_control_chars)

    # Check 5: Split CRLF between calls
    def check_split_crlf():
        dec = ChunkedDecoder()
        slices = [b"4\r", b"\nWiki", b"\r", b"\n0", b"\r\n", b"\r\n"]
        out = bytearray()
        for s in slices:
            out += dec.feed(s)
        dec.finish()
        assert bytes(out) == b"Wiki"
        assert dec.done is True
    run_check("split_crlf", check_split_crlf)

    # Check 6: Case-insensitive hex sizes with leading zeros
    def check_hex_case_and_leading_zeros():
        dec = ChunkedDecoder()
        data = dec.feed(b"000a\r\n0123456789\r\n000B\r\n0123456789a\r\n0000\r\n\r\n")
        dec.finish()
        assert data == b"01234567890123456789a"
        assert dec.done is True
    run_check("hex_case_and_leading_zeros", check_hex_case_and_leading_zeros)

    # Check 7: Chunk extensions parsing
    def check_chunk_extensions():
        dec = ChunkedDecoder()
        data = dec.feed(b"4;ext1=val;ext2\r\ntest\r\n0;final=true\r\n\r\n")
        dec.finish()
        assert data == b"test"
        assert dec.done is True
    run_check("chunk_extensions", check_chunk_extensions)

    # Check 8: Empty chunked message
    def check_empty_message():
        dec = ChunkedDecoder()
        data = dec.feed(b"0\r\n\r\n")
        dec.finish()
        assert data == b""
        assert dec.done is True
    run_check("empty_message", check_empty_message)

    # Check 9: Trailers section with valid headers
    def check_trailers_section():
        dec = ChunkedDecoder()
        stream = b"4\r\nbody\r\n0\r\nExpires: Wed, 21 Oct 2025 07:28:00 GMT\r\nX-Checksum: 12345\r\n\r\n"
        out = dec.feed(stream)
        dec.finish()
        assert out == b"body"
        assert dec.done is True
        assert hasattr(dec, "trailers")
        assert dec.trailers.get(b"expires") == b"Wed, 21 Oct 2025 07:28:00 GMT"
        assert dec.trailers.get(b"x-checksum") == b"12345"
    run_check("trailers_section", check_trailers_section)

    # Check 10: Non-hex characters in chunk size raises MalformedFramingError
    def check_non_hex_size():
        dec = ChunkedDecoder()
        try:
            dec.feed(b"1G\r\nhello\r\n0\r\n\r\n")
            assert False, "Did not raise MalformedFramingError"
        except MalformedFramingError:
            pass
    run_check("non_hex_size", check_non_hex_size)

    # Check 11: Missing CRLF after chunk data raises MalformedFramingError
    def check_missing_crlf_after_chunk():
        dec = ChunkedDecoder()
        try:
            dec.feed(b"4\r\nhellXX0\r\n\r\n")
            assert False, "Did not raise MalformedFramingError"
        except MalformedFramingError:
            pass
    run_check("missing_crlf_after_chunk", check_missing_crlf_after_chunk)

    # Check 12: Negative or signed chunk size raises MalformedFramingError
    def check_signed_chunk_size():
        dec = ChunkedDecoder()
        try:
            dec.feed(b"+4\r\ntest\r\n0\r\n\r\n")
            assert False, "Did not raise MalformedFramingError"
        except MalformedFramingError:
            pass
    run_check("signed_chunk_size", check_signed_chunk_size)

    # Check 13: max_chunk_size exceeded raises SizeLimitExceededError
    def check_max_chunk_size_limit():
        dec = ChunkedDecoder(max_chunk_size=100)
        try:
            dec.feed(b"65\r\n" + b"a" * 101 + b"\r\n0\r\n\r\n")
            assert False, "Did not raise SizeLimitExceededError"
        except SizeLimitExceededError:
            pass
    run_check("max_chunk_size_limit", check_max_chunk_size_limit)

    # Check 14: max_header_bytes exceeded raises SizeLimitExceededError
    def check_max_header_bytes_limit():
        dec = ChunkedDecoder(max_header_bytes=15)
        try:
            dec.feed(b"4;very_long_extension_exceeding_fifteen_bytes=yes\r\ntest\r\n0\r\n\r\n")
            assert False, "Did not raise SizeLimitExceededError"
        except SizeLimitExceededError:
            pass
    run_check("max_header_bytes_limit", check_max_header_bytes_limit)

    # Check 15: max_trailer_bytes exceeded raises SizeLimitExceededError
    def check_max_trailer_bytes_limit():
        dec = ChunkedDecoder(max_trailer_bytes=30)
        stream = b"0\r\nX-Long-Trailer: This trailer line exceeds the 30 bytes limit easily\r\n\r\n"
        try:
            dec.feed(stream)
            assert False, "Did not raise SizeLimitExceededError"
        except SizeLimitExceededError:
            pass
    run_check("max_trailer_bytes_limit", check_max_trailer_bytes_limit)

    # Check 16: feed non-bytes argument raises TypeError
    def check_non_bytes_feed():
        dec = ChunkedDecoder()
        try:
            dec.feed("string instead of bytes")  # type: ignore
            assert False, "Did not raise TypeError"
        except TypeError:
            pass
    run_check("non_bytes_feed", check_non_bytes_feed)

    # Check 17: finish() on incomplete message raises IncompleteMessageError
    def check_finish_incomplete():
        dec = ChunkedDecoder()
        dec.feed(b"5\r\nHel")
        assert not dec.done
        try:
            dec.finish()
            assert False, "Did not raise IncompleteMessageError"
        except IncompleteMessageError:
            pass
    run_check("finish_incomplete", check_finish_incomplete)

    # Check 18: finish() on unclosed final chunk raises IncompleteMessageError
    def check_finish_unclosed_final_chunk():
        dec = ChunkedDecoder()
        dec.feed(b"0\r\n")  # missing terminating CRLF
        assert not dec.done
        try:
            dec.finish()
            assert False, "Did not raise IncompleteMessageError"
        except IncompleteMessageError:
            pass
    run_check("finish_unclosed_final_chunk", check_finish_unclosed_final_chunk)

    # Check 19: feed() after done raises TrailingDataError
    def check_trailing_data_error():
        dec = ChunkedDecoder()
        dec.feed(b"0\r\n\r\n")
        assert dec.done
        try:
            dec.feed(b"GET /next HTTP/1.1\r\n")
            assert False, "Did not raise TrailingDataError"
        except TrailingDataError:
            pass
    run_check("trailing_data_error", check_trailing_data_error)

    # Check 20: Trailing data included immediately after final CRLF in same feed() raises TrailingDataError
    def check_trailing_data_in_same_feed():
        dec = ChunkedDecoder()
        try:
            dec.feed(b"0\r\n\r\nextra_data")
            assert False, "Did not raise TrailingDataError"
        except TrailingDataError:
            pass
    run_check("trailing_data_in_same_feed", check_trailing_data_in_same_feed)

    # Check 21: finish() is idempotent when done
    def check_finish_idempotent():
        dec = ChunkedDecoder()
        dec.feed(b"0\r\n\r\n")
        assert dec.done
        dec.finish()
        dec.finish()
        assert dec.done
    run_check("finish_idempotent", check_finish_idempotent)

    # Check 22: Error state is latching (further feed() after error raises exception)
    def check_error_state_latching():
        dec = ChunkedDecoder()
        try:
            dec.feed(b"INVALID\r\n")
        except ChunkedDecodeError:
            pass
        # Subsequent feed should also raise
        try:
            dec.feed(b"0\r\n\r\n")
            assert False, "Decoder allowed feed after error state"
        except ChunkedDecodeError:
            pass
    run_check("error_state_latching", check_error_state_latching)

    # Check 23: Disallowed trailer field raises MalformedFramingError
    def check_disallowed_trailer_field():
        dec = ChunkedDecoder()
        # Transfer-Encoding in trailer is forbidden by RFC 9112 Section 7.1.2
        stream = b"0\r\nTransfer-Encoding: chunked\r\n\r\n"
        try:
            dec.feed(stream)
            assert False, "Did not raise MalformedFramingError for forbidden trailer"
        except MalformedFramingError:
            pass
    run_check("disallowed_trailer_field", check_disallowed_trailer_field)

    # Check 24: Multiple trailer lines and duplicate trailer field folding
    def check_multiple_trailers():
        dec = ChunkedDecoder()
        stream = b"3\r\nfoo\r\n0\r\nSet-Cookie: a=1\r\nSet-Cookie: b=2\r\n\r\n"
        out = dec.feed(stream)
        dec.finish()
        assert out == b"foo"
        assert dec.done
        assert hasattr(dec, "trailers")
        # Duplicate field should be joined with comma per RFC 9110 / src/headers.py
        assert dec.trailers.get(b"set-cookie") == b"a=1, b=2"
    run_check("multiple_trailers", check_multiple_trailers)

    if result["hidden_total"] > 0 and result["hidden_passed"] == result["hidden_total"]:
        result["hidden_tests_passed"] = True

    if (
        result["target_exists"]
        and result["single_file_modified"]
        and result["public_tests_passed"]
        and result["hidden_tests_passed"]
    ):
        result["status"] = "PASS"
    else:
        result["status"] = "FAIL"

    return result


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <workspace_dir> <fixture_dir>")
        sys.exit(1)
    res = evaluate_task_5(sys.argv[1], sys.argv[2])
    print(json.dumps(res, indent=2))
    sys.exit(0 if res["status"] == "PASS" else 1)
