from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Iterable


def analyze_logs(lines: Iterable[str]) -> dict:
    total_valid = 0
    total_invalid = 0
    aggregates: dict[str, dict] = {}

    for line in lines:
        if not isinstance(line, str) or not line.strip():
            continue
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("record must be a JSON object")

            route = record.get("route")
            status = record.get("status")
            duration = record.get("duration_ms")
            if not isinstance(route, str) or not route.strip():
                raise ValueError("route must be a nonempty string")
            if type(status) is not int or not 100 <= status <= 599:
                raise ValueError("status must be an integer from 100 to 599")
            if type(duration) not in (int, float) or duration < 0:
                raise ValueError("duration_ms must be a nonnegative number")
            if isinstance(duration, float) and not math.isfinite(duration):
                raise ValueError("duration_ms must be finite")

            route = route.strip()
            aggregate = aggregates.setdefault(
                route, {"count": 0, "errors": 0, "durations": []}
            )
            aggregate["count"] += 1
            aggregate["errors"] += status >= 500
            aggregate["durations"].append(duration)
            total_valid += 1
        except (TypeError, ValueError, OverflowError, RecursionError):
            total_invalid += 1

    routes = []
    for route, aggregate in aggregates.items():
        durations = aggregate["durations"]
        durations.sort()
        count = aggregate["count"]
        p95_index = math.ceil(0.95 * count) - 1
        avg_ms = round(math.fsum(value / count for value in durations), 2)
        routes.append(
            {
                "route": route,
                "count": count,
                "errors": aggregate["errors"],
                "avg_ms": avg_ms,
                "p95_ms": round(float(durations[p95_index]), 2),
            }
        )

    routes.sort(key=lambda item: (-item["count"], item["route"]))
    return {
        "total_valid": total_valid,
        "total_invalid": total_invalid,
        "routes": routes,
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(f"Usage: {Path(sys.argv[0]).name} INPUT.jsonl", file=sys.stderr)
        return 2

    try:
        with open(args[0], "r", encoding="utf-8") as input_file:
            result = analyze_logs(input_file)
    except (OSError, UnicodeError) as exc:
        print(f"Error reading input file {args[0]!r}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, allow_nan=False))
    return 0


class AnalyzeLogsTests(unittest.TestCase):
    @staticmethod
    def record(route="/api/users", status=200, duration_ms=100):
        return json.dumps(
            {"route": route, "status": status, "duration_ms": duration_ms}
        )

    def test_aggregation_counts_and_averages(self):
        result = analyze_logs(
            [
                self.record(" /api/users ", duration_ms=100),
                self.record("/api/users", duration_ms=150),
                self.record("/api/users", duration_ms=110),
            ]
        )
        self.assertEqual((result["total_valid"], result["total_invalid"]), (3, 0))
        self.assertEqual(
            result["routes"][0],
            {
                "route": "/api/users",
                "count": 3,
                "errors": 0,
                "avg_ms": 120.0,
                "p95_ms": 150.0,
            },
        )

    def test_counts_http_5xx_errors(self):
        result = analyze_logs(
            [
                self.record(status=499),
                self.record(status=500),
                self.record(status=599),
            ]
        )
        self.assertEqual(result["routes"][0]["errors"], 2)

    def test_nearest_rank_p95(self):
        result = analyze_logs(
            [self.record(duration_ms=value) for value in range(1, 21)]
        )
        self.assertEqual(result["routes"][0]["p95_ms"], 19.0)

    def test_malformed_json_lines_are_invalid(self):
        result = analyze_logs(["{", "not json", "[]"])
        self.assertEqual(result, {"total_valid": 0, "total_invalid": 3, "routes": []})

    def test_field_validation_rejects_invalid_types_and_values(self):
        invalid_records = [
            {"route": "  ", "status": 200, "duration_ms": 1},
            {"route": 3, "status": 200, "duration_ms": 1},
            {"route": "/x", "status": True, "duration_ms": 1},
            {"route": "/x", "status": 99, "duration_ms": 1},
            {"route": "/x", "status": 600, "duration_ms": 1},
            {"route": "/x", "status": 200, "duration_ms": True},
            {"route": "/x", "status": 200, "duration_ms": -1},
            {"route": "/x", "status": 200, "duration_ms": "1"},
            {"route": "/x", "status": 200, "duration_ms": float("nan")},
            {"route": "/x", "status": 200, "duration_ms": float("inf")},
        ]
        lines = [json.dumps(record) for record in invalid_records]
        lines.append(self.record())
        result = analyze_logs(lines)
        self.assertEqual((result["total_valid"], result["total_invalid"]), (1, 10))

    def test_blank_lines_are_ignored(self):
        result = analyze_logs(["", " \t\n", self.record()])
        self.assertEqual((result["total_valid"], result["total_invalid"]), (1, 0))

    def test_routes_sort_by_count_then_name(self):
        result = analyze_logs(
            [
                self.record("/z"),
                self.record("/a"),
                self.record("/z"),
                self.record("/b"),
                self.record("/a"),
            ]
        )
        self.assertEqual(
            [route["route"] for route in result["routes"]], ["/a", "/z", "/b"]
        )

    def test_empty_input(self):
        self.assertEqual(
            analyze_logs([]), {"total_valid": 0, "total_invalid": 0, "routes": []}
        )

    def test_cli_reports_unreadable_input_without_traceback(self):
        script = Path(__file__).resolve()
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.jsonl"
            result = subprocess.run(
                [sys.executable, str(script), str(missing)],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Error reading input file", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        unittest.main(argv=[sys.argv[0]])
    else:
        raise SystemExit(main())
