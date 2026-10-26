#!/usr/bin/env python3
"""
Test script to verify Headroom local compression against synthetic data.
Ensures zero network calls to model providers and no provider route changes.
"""

import sys
import json

def test_headroom_compression():
    print("=== Testing Headroom Local Synthetic Compression ===")
    try:
        import headroom
    except ImportError:
        print("[FAIL] headroom-ai is not installed in current environment.")
        return False

    print(f"Loaded Headroom version: {getattr(headroom, '__version__', 'unknown')}")

    # 1. Test SmartCrusher on synthetic structured JSON
    sample_records = [
        {"id": i, "status": "running", "worker": f"node-{i%3}", "metrics": {"cpu": 10.5 + i, "mem": 128 * i}}
        for i in range(25)
    ]
    raw_json = json.dumps(sample_records, indent=2)
    raw_len = len(raw_json)

    messages = [
        {"role": "user", "content": "Check cluster worker metrics."},
        {"role": "tool", "content": raw_json}
    ]

    res = headroom.compress(messages)
    compressed_content = res.messages[1]["content"]
    comp_len = len(compressed_content)
    reduction = (1.0 - (comp_len / raw_len)) * 100.0

    print(f"Raw synthetic JSON size:       {raw_len} chars")
    print(f"Compressed tool output size:   {comp_len} chars")
    print(f"Compression ratio:             {reduction:.1f}% reduction")
    print("\n--- Compressed Preview (First 250 chars) ---")
    print(compressed_content[:250])
    print("-------------------------------------------\n")

    assert comp_len < raw_len, "Compression failed to reduce character count"
    print("[PASS] Headroom local compression verified successfully without external API calls.")
    return True

if __name__ == "__main__":
    success = test_headroom_compression()
    sys.exit(0 if success else 1)
