import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Iterable


def analyze_logs(lines: Iterable[str]) -> dict:
    """Validate JSONL request records and aggregate metrics by trimmed route."""
    routes = {}
    total_valid = 0
    total_invalid = 0

    for line in lines:
        if not isinstance(line, str):
            total_invalid += 1
            continue
        if not line.strip():
            continue

        try:
            record = json.loads(line)
        except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
            total_invalid += 1
            continue

        if not isinstance(record, dict):
            total_invalid += 1
            continue

        route = record.get("route")
        status = record.get("status")
        duration = record.get("duration_ms")
        if (
            not isinstance(route, str)
            or not route.strip()
            or type(status) is not int
            or not 100 <= status <= 599
            or type(duration) not in (int, float)
            or duration < 0
            or (isinstance(duration, float) and not math.isfinite(duration))
        ):
            total_invalid += 1
            continue

        route = route.strip()
        stats = routes.setdefault(route, {"count": 0, "errors": 0, "durations": []})
        stats["count"] += 1
        stats["errors"] += status >= 500
        stats["durations"].append(duration)
        total_valid += 1

    result_routes = []
    for route, stats in routes.items():
        durations = sorted(stats["durations"])
        count = stats["count"]
        result_routes.append(
            {
                "route": route,
                "count": count,
                "errors": stats["errors"],
                "avg_ms": round(math.fsum(value / count for value in durations), 2),
                "p95_ms": round(durations[math.ceil(0.95 * count) - 1], 2),
            }
        )

    result_routes.sort(key=lambda item: (-item["count"], item["route"]))
    return {
        "total_valid": total_valid,
        "total_invalid": total_invalid,
        "routes": result_routes,
    }


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Analyze HTTP request logs in JSONL format.")
    parser.add_argument("input", help="path to the input JSONL file")
    args = parser.parse_args(argv)

    try:
        with Path(args.input).open("r", encoding="utf-8") as input_file:
            result = analyze_logs(input_file)
    except (OSError, UnicodeError) as exc:
        print(f"Error: cannot read input file '{args.input}': {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, allow_nan=False))
    return 0


class AnalyzeLogsTests(unittest.TestCase):
    @staticmethod
    def record(route="/api/users", status=200, duration=100):
        return json.dumps({"route": route, "status": status, "duration_ms": duration})

    def test_aggregation_counts_and_averages(self):
        result = analyze_logs(
            [self.record(duration=100), self.record(route=" /api/users ", duration=140), "{bad"]
        )
        self.assertEqual((result["total_valid"], result["total_invalid"]), (2, 1))
        self.assertEqual(result["routes"][0]["count"], 2)
        self.assertEqual(result["routes"][0]["avg_ms"], 120.0)
        self.assertEqual(result["routes"][0]["route"], "/api/users")

    def test_http_5xx_error_counting(self):
        result = analyze_logs(
            [self.record(status=499), self.record(status=500), self.record(status=599), self.record(status=600)]
        )
        self.assertEqual(result["total_valid"], 3)
        self.assertEqual(result["total_invalid"], 1)
        self.assertEqual(result["routes"][0]["errors"], 2)

    def test_nearest_rank_p95(self):
        result = analyze_logs([self.record(duration=value) for value in (1, 2, 3, 4)])
        self.assertEqual(result["routes"][0]["p95_ms"], 4.0)

    def test_malformed_json_is_invalid(self):
        result = analyze_logs(["{", "[]", "null", self.record()])
        self.assertEqual((result["total_valid"], result["total_invalid"]), (1, 3))

    def test_field_validation_including_boolean_and_nonfinite_numbers(self):
        invalid = [
            {"route": " ", "status": 200, "duration_ms": 1},
            {"route": 3, "status": 200, "duration_ms": 1},
            {"route": "/x", "status": True, "duration_ms": 1},
            {"route": "/x", "status": 99, "duration_ms": 1},
            {"route": "/x", "status": 600, "duration_ms": 1},
            {"route": "/x", "status": 200, "duration_ms": True},
            {"route": "/x", "status": 200, "duration_ms": -1},
            {"route": "/x", "status": 200, "duration_ms": "1"},
            {"route": "/x", "status": 200, "duration_ms": float("nan")},
            {"route": "/x", "status": 200, "duration_ms": float("inf")},
            {"route": "/x", "status": 200},
        ]
        lines = [json.dumps(item) for item in invalid]
        lines.append(self.record())
        result = analyze_logs(lines)
        self.assertEqual((result["total_valid"], result["total_invalid"]), (1, len(invalid)))

    def test_blank_lines_are_ignored(self):
        result = analyze_logs(["", " \t\r\n", self.record()])
        self.assertEqual((result["total_valid"], result["total_invalid"]), (1, 0))

    def test_route_sort_count_descending_then_name_ascending(self):
        lines = [
            self.record(route="/z"),
            self.record(route="/b"),
            self.record(route="/a"),
            self.record(route="/a"),
        ]
        self.assertEqual([item["route"] for item in analyze_logs(lines)["routes"]], ["/a", "/b", "/z"])

    def test_empty_input(self):
        self.assertEqual(
            analyze_logs([]), {"total_valid": 0, "total_invalid": 0, "routes": []}
        )

    def test_cli_reports_unreadable_input_without_traceback(self):
        script = Path(__file__).resolve()
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [sys.executable, str(script), directory],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("cannot read input file", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:]:
        unittest.main(argv=[sys.argv[0]])
    else:
        raise SystemExit(main())
