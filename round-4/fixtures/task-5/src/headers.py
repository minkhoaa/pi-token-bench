"""HTTP chunk trailer header parsing and validation utilities (RFC 9112 Section 7.1.2)."""

import re
from typing import Dict, List, Tuple
from .exceptions import MalformedFramingError, SizeLimitExceededError

# RFC 9110 / RFC 9112:
# field-name = token = 1*tchar
# tchar = "!" / "#" / "$" / "%" / "&" / "'" / "*" / "+" / "-" / "." /
#         "^" / "_" / "`" / "|" / "~" / DIGIT / ALPHA
TOKEN_REGEX = re.compile(rb"^[!#$%&'*+\-.^_`|~0-9a-zA-Z]+$")

# RFC 9112 Section 7.1.2: Prohibited trailer fields
# Trailer MUST NOT contain Transfer-Encoding, Content-Length, Host, authorization, etc.
DISALLOWED_TRAILERS = {
    b"transfer-encoding",
    b"content-length",
    b"host",
    b"trailer",
}


def parse_trailer_line(line: bytes) -> Tuple[bytes, bytes]:
    """
    Parse and validate a single trailer field line (bytes).
    Line must NOT contain trailing CRLF.
    Returns (field_name_lower, field_value_stripped).
    Raises MalformedFramingError if invalid.
    """
    if b":" not in line:
        raise MalformedFramingError(f"Trailer line missing colon delimiter: {line!r}")

    name_part, val_part = line.split(b":", 1)

    # RFC 9112 Section 5.1: No whitespace allowed between field name and colon
    if name_part.endswith(b" ") or name_part.endswith(b"\t"):
        raise MalformedFramingError(f"Trailing whitespace before colon in trailer field name: {line!r}")

    if not name_part or not TOKEN_REGEX.match(name_part):
        raise MalformedFramingError(f"Invalid field name in trailer: {name_part!r}")

    name_lower = name_part.lower()
    if name_lower in DISALLOWED_TRAILERS:
        raise MalformedFramingError(f"Disallowed trailer field: {name_lower.decode('latin1', errors='replace')}")

    # RFC 9112 Section 5.1: field-value = *( field-vchar / [ RWS ] )
    # Strip leading/trailing OWS (SP or HTAB)
    val_stripped = val_part.strip(b" \t")

    # Check for invalid control characters (NUL, bare CR, bare LF)
    for b in val_stripped:
        if b < 0x20 and b != 0x09:  # HTAB is 0x09
            raise MalformedFramingError(f"Invalid control character in trailer field value: {b:#x}")

    return name_lower, val_stripped


def parse_trailers_section(trailer_bytes: bytes, max_trailer_bytes: int = 16384) -> Dict[bytes, bytes]:
    """
    Parse a raw trailers block (excluding the final terminating CRLF).
    Raises SizeLimitExceededError if trailer_bytes exceeds max_trailer_bytes.
    Raises MalformedFramingError on parsing failures.
    """
    if len(trailer_bytes) > max_trailer_bytes:
        raise SizeLimitExceededError(
            f"Trailer section size {len(trailer_bytes)} exceeds maximum {max_trailer_bytes}"
        )

    if not trailer_bytes:
        return {}

    trailers: Dict[bytes, bytes] = {}
    lines = trailer_bytes.split(b"\r\n")

    for line in lines:
        if not line:
            continue
        # Obsolete line folding (starts with SP or HTAB) is disallowed in HTTP/1.1 (RFC 9112 Section 5.2)
        if line[0:1] in (b" ", b"\t"):
            raise MalformedFramingError("Obsolete line folding in trailer is not supported")

        name, val = parse_trailer_line(line)
        if name in trailers:
            # Append comma-separated per RFC 9110 Section 5.3
            trailers[name] = trailers[name] + b", " + val
        else:
            trailers[name] = val

    return trailers
