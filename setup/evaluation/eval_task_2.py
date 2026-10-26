#!/usr/bin/env python3
"""
Independent external evaluator for Task 2: Fault-Tolerant Async DAG Scheduler.
Evaluates single deliverable file constraint, self-test execution, and a fixed external test suite.
"""

import sys
sys.dont_write_bytecode = True
import os
import json
import asyncio
import subprocess
import hashlib
import importlib.util
from pathlib import Path


def evaluate_task_2(target_dir: str) -> dict:
    target_path = Path(target_dir).resolve()
    folder_name = target_path.name
    expected_filename = f"task-2-{folder_name}.py"
    deliverable_path = target_path / expected_filename

    result = {
        "task": "task-2",
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

    # 1. File existence & single deliverable check
    if not deliverable_path.is_file():
        result["failures"].append(f"Deliverable file '{expected_filename}' not found in {target_path}")
        return result

    result["deliverable_exists"] = True

    py_files = list(target_path.glob("*.py"))
    if len(py_files) == 1 and py_files[0].name == expected_filename:
        result["single_deliverable"] = True
    else:
        result["failures"].append(
            f"Expected exactly 1 deliverable .py file, found {len(py_files)}: {[f.name for f in py_files]}"
        )

    content = deliverable_path.read_bytes()
    result["deliverable_sha256"] = hashlib.sha256(content).hexdigest()
    result["line_count"] = len(content.splitlines())

    # 2. Run self-tests via subprocess
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

    # 3. Dynamic import of deliverable module
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

    for req_attr in ("Job", "JobResult", "run_dag"):
        if not hasattr(module, req_attr):
            result["failures"].append(f"Module missing required attribute '{req_attr}'")
            return result

    Job = getattr(module, "Job")
    JobResult = getattr(module, "JobResult")
    run_dag = getattr(module, "run_dag")

    # 4. Async external test cases runner
    async def run_all_async_tests():
        def run_case(name: str, coro_fn):
            result["external_total"] += 1
            try:
                loop = asyncio.get_running_loop()
                # Run with timeout
                fut = asyncio.wait_for(coro_fn(), timeout=10.0)
                loop.run_until_complete(fut) if not loop.is_running() else None
            except Exception:
                raise

        # Helper to execute a single async test with timeout
        async def exec_case(name: str, coro_fn):
            result["external_total"] += 1
            try:
                await asyncio.wait_for(coro_fn(), timeout=10.0)
                result["external_passed"] += 1
            except Exception as e:
                result["failures"].append(f"External test '{name}' failed: {e}")

        # Case 1: Empty graph
        async def test_empty():
            res = await run_dag({}, lambda j: None)
            assert res == {}, f"Empty graph did not return empty dict: {res}"
        await exec_case("empty_graph", test_empty)

        # Case 2: Linear chain & dependency ordering
        async def test_linear():
            execution_order = []
            async def worker(job_id):
                execution_order.append(job_id)
                return f"done-{job_id}"

            jobs = {
                "step1": Job(),
                "step2": Job(deps=("step1",)),
                "step3": Job(deps=("step2",)),
            }
            res = await run_dag(jobs, worker, max_concurrency=2)
            assert execution_order == ["step1", "step2", "step3"], f"Wrong execution order: {execution_order}"
            assert res["step3"].status == "completed"
            assert res["step3"].value == "done-step3"
        await exec_case("linear_chain", test_linear)

        # Case 3: Concurrency enforcement
        async def test_concurrency():
            current_active = 0
            max_active_seen = 0
            lock = asyncio.Lock()

            async def worker(job_id):
                nonlocal current_active, max_active_seen
                async with lock:
                    current_active += 1
                    if current_active > max_active_seen:
                        max_active_seen = current_active
                await asyncio.sleep(0.05)
                async with lock:
                    current_active -= 1
                return job_id

            jobs = {f"job_{i}": Job() for i in range(6)}
            res = await run_dag(jobs, worker, max_concurrency=2)
            assert max_active_seen <= 2, f"Max concurrency exceeded: saw {max_active_seen} > 2"
            assert all(r.status == "completed" for r in res.values())
        await exec_case("concurrency_limit", test_concurrency)

        # Case 4: Diamond dependency graph
        async def test_diamond():
            completed = set()
            async def worker(job_id):
                if job_id == "D":
                    assert "B" in completed and "C" in completed, "D ran before B or C completed"
                completed.add(job_id)
                return job_id

            jobs = {
                "A": Job(),
                "B": Job(deps=("A",)),
                "C": Job(deps=("A",)),
                "D": Job(deps=("B", "C")),
            }
            res = await run_dag(jobs, worker, max_concurrency=4)
            assert res["D"].status == "completed"
        await exec_case("diamond_graph", test_diamond)

        # Case 5: Worker failure and transitive skipping
        async def test_failure_propagation():
            executed = []
            async def worker(job_id):
                executed.append(job_id)
                if job_id == "failing":
                    raise RuntimeError("boom")
                return "ok"

            jobs = {
                "failing": Job(),
                "child": Job(deps=("failing",)),
                "grandchild": Job(deps=("child",)),
                "independent": Job(),
            }
            res = await run_dag(jobs, worker, max_concurrency=3)
            assert res["failing"].status == "failed"
            assert "RuntimeError: boom" in str(res["failing"].error)
            assert res["child"].status == "skipped"
            assert res["grandchild"].status == "skipped"
            assert res["independent"].status == "completed"
            assert "child" not in executed
            assert "grandchild" not in executed
        await exec_case("failure_propagation", test_failure_propagation)

        # Case 6: Preserved input order in output dict
        async def test_input_order():
            jobs = {
                "z": Job(),
                "a": Job(),
                "m": Job(),
                "b": Job(),
            }
            res = await run_dag(jobs, lambda j: asyncio.sleep(0.01))
            assert list(res.keys()) == ["z", "a", "m", "b"], f"Output order mismatch: {list(res.keys())}"
        await exec_case("preserved_input_order", test_input_order)

        # Case 7: Graph validation checks
        async def test_validation():
            dummy = lambda j: None
            # Invalid concurrency
            for bad_c in (0, -1, True, False, "3", 1.5):
                try:
                    await run_dag({"a": Job()}, dummy, max_concurrency=bad_c)
                    assert False, f"Did not raise ValueError on bad concurrency: {bad_c}"
                except ValueError:
                    pass

            # Cycle
            try:
                await run_dag({"a": Job(deps=("b",)), "b": Job(deps=("a",))}, dummy)
                assert False, "Did not raise ValueError on cycle"
            except ValueError:
                pass

            # Self-dep
            try:
                await run_dag({"a": Job(deps=("a",))}, dummy)
                assert False, "Did not raise ValueError on self-dep"
            except ValueError:
                pass

            # Missing dep
            try:
                await run_dag({"a": Job(deps=("missing",))}, dummy)
                assert False, "Did not raise ValueError on missing dep"
            except ValueError:
                pass

            # Duplicate dep
            try:
                await run_dag({"a": Job(), "b": Job(deps=("a", "a"))}, dummy)
                assert False, "Did not raise ValueError on duplicate dep"
            except ValueError:
                pass

            # Non-tuple dep
            try:
                await run_dag({"a": Job(), "b": Job(deps=["a"])}, dummy)
                assert False, "Did not raise ValueError on non-tuple deps"
            except (ValueError, TypeError):
                pass

            # Non-callable worker
            try:
                await run_dag({"a": Job()}, "not callable")
                assert False, "Did not raise ValueError on non-callable worker"
            except ValueError:
                pass

        await exec_case("graph_validation", test_validation)

        # Case 8: Cancellation cleanup
        async def test_cancellation():
            cancelled_workers = []
            worker_started = asyncio.Event()

            async def worker(job_id):
                worker_started.set()
                try:
                    await asyncio.sleep(10.0)
                except asyncio.CancelledError:
                    cancelled_workers.append(job_id)
                    raise

            jobs = {"slow1": Job(), "slow2": Job()}
            task = asyncio.create_task(run_dag(jobs, worker, max_concurrency=2))
            await worker_started.wait()
            # Allow second worker a tick to start
            await asyncio.sleep(0.02)
            task.cancel()

            cancelled_propagated = False
            try:
                await task
            except asyncio.CancelledError:
                cancelled_propagated = True

            assert cancelled_propagated, "run_dag did not propagate asyncio.CancelledError"
            assert len(cancelled_workers) > 0, "No child workers were cancelled"
        await exec_case("cancellation_cleanup", test_cancellation)

    asyncio.run(run_all_async_tests())

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
        print("Usage: eval_task_2.py <target-task-dir>")
        sys.exit(1)
    res = evaluate_task_2(sys.argv[1])
    print(json.dumps(res, indent=2))
    sys.exit(0 if res["overall_passed"] else 1)
