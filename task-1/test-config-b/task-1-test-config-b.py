#!/usr/bin/env python3
"""Streaming JSONL HTTP log analyzer."""

import json
import math
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Iterable


def analyze_logs(lines: Iterable[str]) -> dict:
    total_valid = 0
    total_invalid = 0
    routes = {}

    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("record must be a JSON object")
            route = record["route"]
            status = record["status"]
            duration = record["duration_ms"]
            if not isinstance(route, str) or not route.strip():
                raise ValueError("route must be a nonempty string")
            if type(status) is not int or not 100 <= status <= 599:
                raise ValueError("status must be an integer from 100 to 599")
            if isinstance(duration, bool) or not isinstance(duration, (int, float)):
                raise ValueError("duration_ms must be a number")
            duration = float(duration)
            if not math.isfinite(duration) or duration < 0:
                raise ValueError("duration_ms must be finite and nonnegative")
        except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
            total_invalid += 1
            continue

        route = route.strip()
        aggregate = routes.setdefault(route, {"count": 0, "errors": 0, "durations": []})
        aggregate["count"] += 1
        aggregate["errors"] += status >= 500
        aggregate["durations"].append(duration)
        total_valid += 1

    result_routes = []
    for route, aggregate in sorted(routes.items(), key=lambda item: (-item[1]["count"], item[0])):
        durations = aggregate["durations"]
        durations.sort()
        count = aggregate["count"]
        p95_index = math.ceil(0.95 * count) - 1
        result_routes.append({
            "route": route,
            "count": count,
            "errors": aggregate["errors"],
            "avg_ms": round(math.fsum(value / count for value in durations), 2),
            "p95_ms": round(durations[p95_index], 2),
        })

    return {
        "total_valid": total_valid,
        "total_invalid": total_invalid,
        "routes": result_routes,
    }


class AnalyzeLogsTests(unittest.TestCase):
    def test_aggregation_totals_and_averages(self):
        result = analyze_logs([
            '{"route":" /api/users ","status":200,"duration_ms":100}',
            '{"route":"/api/users","status":201,"duration_ms":200}',
            '{"route":"/api/users","status":503,"duration_ms":50}',
        ])
        self.assertEqual(result["total_valid"], 3)
        self.assertEqual(result["total_invalid"], 0)
        self.assertEqual(result["routes"][0]["route"], "/api/users")
        self.assertEqual(result["routes"][0]["avg_ms"], 116.67)

    def test_http_5xx_error_counting(self):
        result = analyze_logs([
            '{"route":"/","status":200,"duration_ms":1}',
            '{"route":"/","status":500,"duration_ms":1}',
            '{"route":"/","status":599,"duration_ms":1}',
        ])
        self.assertEqual(result["routes"][0]["errors"], 2)

    def test_nearest_rank_p95(self):
        lines = [
            json.dumps({"route": "/", "status": 200, "duration_ms": value})
            for value in range(1, 21)
        ]
        self.assertEqual(analyze_logs(lines)["routes"][0]["p95_ms"], 19.0)

    def test_malformed_json_lines_are_invalid(self):
        result = analyze_logs(["{", "not json", '{"route":"/","status":200,"duration_ms":1}'])
        self.assertEqual((result["total_valid"], result["total_invalid"]), (1, 2))

    def test_field_validation(self):
        valid = {"route": "/", "status": 200, "duration_ms": 1}
        invalid_records = [
            {**valid, "route": "   "},
            {**valid, "route": 4},
            {**valid, "status": True},
            {**valid, "status": 99},
            {**valid, "status": 600},
            {**valid, "duration_ms": False},
            {**valid, "duration_ms": -1},
            {**valid, "duration_ms": float("nan")},
            {**valid, "duration_ms": float("inf")},
            {"route": "/", "status": 200},
            [],
        ]
        result = analyze_logs([json.dumps(record) for record in [valid, *invalid_records]])
        self.assertEqual((result["total_valid"], result["total_invalid"]), (1, len(invalid_records)))

    def test_blank_lines_are_ignored(self):
        result = analyze_logs(["", " \t\n", '{"route":"/","status":200,"duration_ms":3}'])
        self.assertEqual((result["total_valid"], result["total_invalid"]), (1, 0))

    def test_route_sorting_by_count_then_name(self):
        lines = [
            json.dumps({"route": route, "status": 200, "duration_ms": 1})
            for route in ("/z", "/b", "/a", "/b", "/a", "/c")
        ]
        self.assertEqual([route["route"] for route in analyze_logs(lines)["routes"]], ["/a", "/b", "/c", "/z"])

    def test_empty_input(self):
        self.assertEqual(analyze_logs([]), {"total_valid": 0, "total_invalid": 0, "routes": []})

    def test_cli_unreadable_input_has_no_traceback(self):
        missing = Path(__file__).resolve().parent / f".missing-input-{os.getpid()}"
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(missing)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertTrue(completed.stderr.strip())
        self.assertNotIn("Traceback", completed.stderr)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--self-test"]:
        unittest.main(argv=[sys.argv[0]])
        return 0
    if len(argv) != 1:
        print(f"Usage: {Path(sys.argv[0]).name} INPUT.jsonl (or --self-test)", file=sys.stderr)
        return 2

    input_path = argv[0]
    try:
        with open(input_path, "r", encoding="utf-8") as input_file:
            result = analyze_logs(input_file)
    except (OSError, UnicodeError) as error:
        print(f"Error reading input file {input_path!r}: {error}", file=sys.stderr)
        return 1

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
