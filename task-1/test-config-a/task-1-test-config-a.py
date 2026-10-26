#!/usr/bin/env python3
"""Streaming JSONL HTTP log analyzer."""

import json
import math
import subprocess
import sys
import tempfile
import unittest
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from pathlib import Path
from typing import Iterable


def _reject_json_constant(value: str):
    raise ValueError(f"Invalid JSON constant: {value}")


def _average_ms(durations: list[int | float]):
    count = len(durations)
    try:
        return round(math.fsum(value / count for value in durations), 2)
    except OverflowError:
        # Python ints are unbounded and finite; use Decimal if converting one to
        # float for the average would overflow.
        decimal_values = [Decimal(str(value)) for value in durations]
        precision = max(28, max(value.adjusted() for value in decimal_values) + len(str(count)) + 5)
        with localcontext() as context:
            context.prec = precision
            average = (sum(decimal_values, Decimal(0)) / Decimal(count)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_EVEN
            )
        try:
            as_float = float(average)
        except OverflowError:
            as_float = math.inf
        if math.isfinite(as_float):
            return as_float
        if average == average.to_integral_value():
            return int(average)
        return average


def _json_dumps(value) -> str:
    """Serialize the result while preserving Decimal values as JSON numbers."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        members = (f"{json.dumps(key)}:{_json_dumps(item)}" for key, item in value.items())
        return "{" + ",".join(members) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_json_dumps(item) for item in value) + "]"
    return json.dumps(value, allow_nan=False)


def analyze_logs(lines: Iterable[str]) -> dict:
    """Validate and aggregate JSONL request records in a single pass."""
    total_valid = 0
    total_invalid = 0
    aggregates = defaultdict(lambda: {"count": 0, "errors": 0, "durations": []})

    for line in lines:
        if not line.strip():
            continue

        try:
            record = json.loads(line, parse_constant=_reject_json_constant)
        except (ValueError, RecursionError):
            total_invalid += 1
            continue

        if not isinstance(record, dict):
            total_invalid += 1
            continue

        route = record.get("route")
        status = record.get("status")
        duration = record.get("duration_ms")

        if not isinstance(route, str):
            total_invalid += 1
            continue
        route = route.strip()
        if not route:
            total_invalid += 1
            continue
        if type(status) is not int or not 100 <= status <= 599:
            total_invalid += 1
            continue
        if type(duration) not in (int, float) or duration < 0:
            total_invalid += 1
            continue
        if isinstance(duration, float) and not math.isfinite(duration):
            total_invalid += 1
            continue

        total_valid += 1
        aggregate = aggregates[route]
        aggregate["count"] += 1
        if 500 <= status <= 599:
            aggregate["errors"] += 1
        aggregate["durations"].append(duration)

    routes = []
    for route, aggregate in aggregates.items():
        count = aggregate["count"]
        durations = aggregate["durations"]
        sorted_durations = sorted(durations)
        average = _average_ms(durations)
        p95_index = (95 * count + 99) // 100 - 1
        p95_value = sorted_durations[p95_index]
        try:
            p95_ms = round(float(p95_value), 2)
        except OverflowError:
            # A huge integer is still a valid finite duration; rounding it
            # leaves the integer unchanged.
            p95_ms = round(p95_value, 2)
        routes.append(
            {
                "route": route,
                "count": count,
                "errors": aggregate["errors"],
                "avg_ms": average,
                "p95_ms": p95_ms,
            }
        )

    routes.sort(key=lambda item: (-item["count"], item["route"]))
    return {
        "total_valid": total_valid,
        "total_invalid": total_invalid,
        "routes": routes,
    }


def _run_cli() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
        unittest.main(argv=[sys.argv[0]])
        return 0
    if len(sys.argv) != 2:
        print(f"Usage: {Path(sys.argv[0]).name} INPUT.jsonl | --self-test", file=sys.stderr)
        return 2

    input_path = sys.argv[1]
    try:
        with open(input_path, "r", encoding="utf-8") as input_file:
            result = analyze_logs(input_file)
    except (OSError, UnicodeError) as exc:
        print(f"Error reading input file '{input_path}': {exc}", file=sys.stderr)
        return 1

    print(_json_dumps(result))
    return 0


class AnalyzeLogsTests(unittest.TestCase):
    def test_aggregation_counts_and_averages(self):
        result = analyze_logs(
            [
                '{"route":"/items","status":200,"duration_ms":100}',
                '{"route":" /items ","status":201,"duration_ms":150}',
                '{"route":"/other","status":200,"duration_ms":50}',
            ]
        )
        self.assertEqual(result["total_valid"], 3)
        self.assertEqual(result["total_invalid"], 0)
        items = next(route for route in result["routes"] if route["route"] == "/items")
        self.assertEqual(items["count"], 2)
        self.assertEqual(items["avg_ms"], 125.0)

    def test_http_5xx_error_counting(self):
        result = analyze_logs(
            [
                '{"route":"/","status":500,"duration_ms":1}',
                '{"route":"/","status":599,"duration_ms":2}',
                '{"route":"/","status":499,"duration_ms":3}',
            ]
        )
        self.assertEqual(result["routes"][0]["errors"], 2)

    def test_nearest_rank_p95(self):
        lines = [
            json.dumps({"route": "/", "status": 200, "duration_ms": value})
            for value in range(1, 21)
        ]
        result = analyze_logs(lines)
        self.assertEqual(result["routes"][0]["p95_ms"], 19.0)

    def test_malformed_json_lines_are_invalid(self):
        result = analyze_logs(["{", "not json", "[]", '{"route":"/","status":200,"duration_ms":1}'])
        self.assertEqual(result["total_valid"], 1)
        self.assertEqual(result["total_invalid"], 3)

    def test_field_validation_including_boolean_and_numeric_bounds(self):
        invalid_records = [
            {"route": " ", "status": 200, "duration_ms": 1},
            {"route": 3, "status": 200, "duration_ms": 1},
            {"route": "/", "status": True, "duration_ms": 1},
            {"route": "/", "status": False, "duration_ms": 1},
            {"route": "/", "status": 99, "duration_ms": 1},
            {"route": "/", "status": 600, "duration_ms": 1},
            {"route": "/", "status": 200, "duration_ms": True},
            {"route": "/", "status": 200, "duration_ms": False},
            {"route": "/", "status": 200},
            {"route": "/", "status": 200, "duration_ms": -0.1},
            {"route": "/", "status": 200, "duration_ms": float("nan")},
            {"route": "/", "status": 200, "duration_ms": float("inf")},
            {"route": "/", "status": 200, "duration_ms": "1"},
        ]
        result = analyze_logs(json.dumps(record) for record in invalid_records)
        self.assertEqual(result["total_valid"], 0)
        self.assertEqual(result["total_invalid"], len(invalid_records))
        self.assertEqual(result["routes"], [])

    def test_blank_lines_are_ignored(self):
        result = analyze_logs(["", " \t\r\n", '{"route":"/","status":200,"duration_ms":4}'])
        self.assertEqual(result["total_valid"], 1)
        self.assertEqual(result["total_invalid"], 0)

    def test_routes_sort_by_count_then_name(self):
        lines = [
            '{"route":"/z","status":200,"duration_ms":1}',
            '{"route":"/a","status":200,"duration_ms":1}',
            '{"route":"/z","status":200,"duration_ms":1}',
            '{"route":"/b","status":200,"duration_ms":1}',
            '{"route":"/a","status":200,"duration_ms":1}',
        ]
        self.assertEqual([item["route"] for item in analyze_logs(lines)["routes"]], ["/a", "/z", "/b"])

    def test_empty_input(self):
        self.assertEqual(
            analyze_logs([]), {"total_valid": 0, "total_invalid": 0, "routes": []}
        )

    def test_large_finite_integer_duration_is_accepted(self):
        duration = 10**400
        result = analyze_logs(
            [json.dumps({"route": "/large", "status": 200, "duration_ms": duration})]
        )
        route = result["routes"][0]
        self.assertEqual(result["total_valid"], 1)
        self.assertEqual(route["avg_ms"], duration)
        self.assertEqual(route["p95_ms"], duration)

    def test_cli_fails_cleanly_for_unreadable_input(self):
        script = Path(__file__).resolve()
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [sys.executable, str(script), directory],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("Error reading input file", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)


if __name__ == "__main__":
    raise SystemExit(_run_cli())
