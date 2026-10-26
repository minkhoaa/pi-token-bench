#!/usr/bin/env python3
"""
Independent external evaluator for Task 1: Streaming HTTP Log Analyzer.
Evaluates single deliverable file constraint, self-test execution, and a fixed external test suite.
"""

import sys
sys.dont_write_bytecode = True
import os
import json
import math
import subprocess
import tempfile
import importlib.util
from pathlib import Path


def evaluate_task_1(target_dir: str) -> dict:
    target_path = Path(target_dir).resolve()
    folder_name = target_path.name
    expected_filename = f"task-1-{folder_name}.py"
    deliverable_path = target_path / expected_filename

    result = {
        "task": "task-1",
        "target_dir": str(target_path),
        "expected_filename": expected_filename,
        "deliverable_exists": False,
        "single_deliverable": False,
        "deliverable_sha256": None,
        "line_count": 0,
        "self_tests_passed": False,
        "self_tests_output": "",
        "external_tests_passed": False,
        "external_total": 0,
        "external_passed": 0,
        "failures": [],
        "overall_passed": False,
    }

    # 1. Check file existence & single deliverable rule
    if not deliverable_path.is_file():
        result["failures"].append(f"Deliverable file '{expected_filename}' not found in {target_path}")
        return result

    result["deliverable_exists"] = True

    # Check for other python files
    py_files = list(target_path.glob("*.py"))
    if len(py_files) == 1 and py_files[0].name == expected_filename:
        result["single_deliverable"] = True
    else:
        result["failures"].append(
            f"Expected exactly 1 deliverable .py file, found {len(py_files)}: {[f.name for f in py_files]}"
        )

    # Calculate SHA256 and line count
    import hashlib
    content = deliverable_path.read_bytes()
    result["deliverable_sha256"] = hashlib.sha256(content).hexdigest()
    result["line_count"] = len(content.splitlines())

    # 2. Run self-tests via subprocess with bytecode generation disabled
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(
            [sys.executable, "-B", str(deliverable_path), "--self-test"],
            cwd=str(target_path),
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        combined_output = (proc.stdout + "\n" + proc.stderr).strip()
        result["self_tests_output"] = combined_output
        if proc.returncode == 0:
            result["self_tests_passed"] = True
        else:
            result["failures"].append(f"Self-tests failed with returncode {proc.returncode}")
    except subprocess.TimeoutExpired:
        result["failures"].append("Self-tests timed out after 30 seconds")
    except Exception as e:
        result["failures"].append(f"Self-tests execution error: {e}")

    # 3. Dynamic import of deliverable for external unit tests
    module = None
    try:
        mod_name = f"test_mod_{folder_name}".replace("-", "_")
        spec = importlib.util.spec_from_file_location(mod_name, deliverable_path)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
    except Exception as e:
        result["failures"].append(f"Failed to import deliverable module: {e}")
        return result

    if not hasattr(module, "analyze_logs"):
        result["failures"].append("Module missing required function 'analyze_logs'")
        return result

    analyze_logs = getattr(module, "analyze_logs")

    # 4. External test cases
    external_tests = []

    def run_case(name: str, test_fn):
        result["external_total"] += 1
        try:
            test_fn()
            result["external_passed"] += 1
        except Exception as e:
            result["failures"].append(f"External test '{name}' failed: {e}")

    # Case 1: Empty input
    def test_empty():
        res = analyze_logs([])
        assert res == {"total_valid": 0, "total_invalid": 0, "routes": []}, f"Unexpected empty result: {res}"
    run_case("empty_input", test_empty)

    # Case 2: Basic valid aggregation & rounding
    def test_basic_aggregation():
        lines = [
            json.dumps({"route": "/api/users", "status": 200, "duration_ms": 100.555}),
            json.dumps({"route": "/api/users", "status": 201, "duration_ms": 199.444}),
            json.dumps({"route": "/api/posts", "status": 500, "duration_ms": 50.0}),
        ]
        res = analyze_logs(lines)
        assert res["total_valid"] == 3, f"total_valid != 3: {res}"
        assert res["total_invalid"] == 0, f"total_invalid != 0: {res}"
        assert len(res["routes"]) == 2, f"Expected 2 routes: {res}"
        users = next(r for r in res["routes"] if r["route"] == "/api/users")
        posts = next(r for r in res["routes"] if r["route"] == "/api/posts")
        assert users["count"] == 2
        assert users["errors"] == 0
        assert users["avg_ms"] == round((100.555 + 199.444) / 2, 2)
        assert posts["count"] == 1
        assert posts["errors"] == 1
    run_case("basic_aggregation", test_basic_aggregation)

    # Case 3: Nearest-rank P95 calculation
    def test_p95():
        # Test with 20 values: 1, 2, ..., 20. ceil(0.95 * 20) = 19 -> 0-based index 18 -> value 19
        lines = [
            json.dumps({"route": "/p95", "status": 200, "duration_ms": float(i)})
            for i in range(1, 21)
        ]
        res = analyze_logs(lines)
        route = res["routes"][0]
        assert route["p95_ms"] == 19.0, f"P95 calculation expected 19.0, got {route['p95_ms']}"
    run_case("nearest_rank_p95", test_p95)

    # Case 4: Status 5xx boundaries and errors
    def test_status_boundaries():
        lines = [
            json.dumps({"route": "/status", "status": 499, "duration_ms": 10}),
            json.dumps({"route": "/status", "status": 500, "duration_ms": 10}),
            json.dumps({"route": "/status", "status": 599, "duration_ms": 10}),
            json.dumps({"route": "/status", "status": 600, "duration_ms": 10}),  # invalid
            json.dumps({"route": "/status", "status": 99, "duration_ms": 10}),   # invalid
        ]
        res = analyze_logs(lines)
        assert res["total_valid"] == 3
        assert res["total_invalid"] == 2
        route = res["routes"][0]
        assert route["count"] == 3
        assert route["errors"] == 2  # 500 and 599
    run_case("status_boundaries", test_status_boundaries)

    # Case 5: Boolean type rejection
    def test_boolean_rejection():
        lines = [
            json.dumps({"route": "/bool", "status": True, "duration_ms": 100}),  # invalid status
            json.dumps({"route": "/bool", "status": 200, "duration_ms": False}), # invalid duration
            json.dumps({"route": "/bool", "status": 200, "duration_ms": True}),  # invalid duration
        ]
        res = analyze_logs(lines)
        assert res["total_valid"] == 0, f"Booleans accepted as valid: {res}"
        assert res["total_invalid"] == 3
    run_case("boolean_rejection", test_boolean_rejection)

    # Case 6: Non-finite and negative durations
    def test_duration_validation():
        lines = [
            json.dumps({"route": "/dur", "status": 200, "duration_ms": -1.0}),
            '{"route": "/dur", "status": 200, "duration_ms": NaN}',
            '{"route": "/dur", "status": 200, "duration_ms": Infinity}',
        ]
        res = analyze_logs(lines)
        assert res["total_valid"] == 0
        assert res["total_invalid"] == 3
    run_case("duration_validation", test_duration_validation)

    # Case 7: Route trimming and empty route rejection
    def test_route_trimming():
        lines = [
            json.dumps({"route": "  /api/v1/test  ", "status": 200, "duration_ms": 10}),
            json.dumps({"route": "/api/v1/test", "status": 200, "duration_ms": 20}),
            json.dumps({"route": "   ", "status": 200, "duration_ms": 10}),
            json.dumps({"route": "", "status": 200, "duration_ms": 10}),
        ]
        res = analyze_logs(lines)
        assert res["total_valid"] == 2
        assert res["total_invalid"] == 2
        assert len(res["routes"]) == 1
        assert res["routes"][0]["route"] == "/api/v1/test"
        assert res["routes"][0]["count"] == 2
    run_case("route_trimming", test_route_trimming)

    # Case 8: Sorting order (count desc, route asc)
    def test_sorting():
        lines = [
            json.dumps({"route": "/c", "status": 200, "duration_ms": 10}),
            json.dumps({"route": "/b", "status": 200, "duration_ms": 10}),
            json.dumps({"route": "/b", "status": 200, "duration_ms": 10}),
            json.dumps({"route": "/a", "status": 200, "duration_ms": 10}),
            json.dumps({"route": "/a", "status": 200, "duration_ms": 10}),
        ]
        res = analyze_logs(lines)
        route_names = [r["route"] for r in res["routes"]]
        assert route_names == ["/a", "/b", "/c"], f"Incorrect sort order: {route_names}"
    run_case("sorting_order", test_sorting)

    # Case 9: Blank lines and malformed JSON
    def test_blank_and_malformed():
        lines = [
            "",
            "   ",
            "\n\t",
            "not a json line",
            '{"incomplete": ',
            json.dumps({"route": "/ok", "status": 200, "duration_ms": 50}),
        ]
        res = analyze_logs(lines)
        assert res["total_valid"] == 1
        assert res["total_invalid"] == 2  # The 2 malformed non-blank lines
    run_case("blank_and_malformed", test_blank_and_malformed)

    # Case 10: CLI execution with file and error handling
    def test_cli():
        with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False) as tf:
            tf.write(json.dumps({"route": "/cli", "status": 200, "duration_ms": 42.0}) + "\n")
            tf_path = tf.name

        try:
            # Normal CLI success
            proc = subprocess.run(
                [sys.executable, "-B", str(deliverable_path), tf_path],
                cwd=str(target_path),
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
            )
            assert proc.returncode == 0, f"CLI exit code {proc.returncode}, stderr: {proc.stderr}"
            data = json.loads(proc.stdout)
            assert data["total_valid"] == 1

            # Missing file error handling
            proc_err = subprocess.run(
                [sys.executable, "-B", str(deliverable_path), "/nonexistent/path/file.jsonl"],
                cwd=str(target_path),
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
            )
            assert proc_err.returncode != 0, "CLI should return non-zero on missing file"
            assert "Traceback" not in proc_err.stderr, f"Traceback leaked on CLI error: {proc_err.stderr}"
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    run_case("cli_smoke_and_error", test_cli)

    if result["external_total"] > 0 and result["external_passed"] == result["external_total"]:
        result["external_tests_passed"] = True

    result["overall_passed"] = (
        result["deliverable_exists"]
        and result["single_deliverable"]
        and result["self_tests_passed"]
        and result["external_tests_passed"]
    )

    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: eval_task_1.py <target-task-dir>")
        sys.exit(1)
    res = evaluate_task_1(sys.argv[1])
    print(json.dumps(res, indent=2))
    sys.exit(0 if res["overall_passed"] else 1)
