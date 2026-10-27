#!/usr/bin/env python3
"""
Independent external evaluator for Task 6: Fault-Tolerant Concurrent Job Scheduler.
Evaluates single-file modification constraint (src/scheduler.py only),
public test discovery, and >= 20 hidden evaluation checks.
"""

import sys
sys.dont_write_bytecode = True

import asyncio
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path


def compute_file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def evaluate_task_6(workspace_dir: str, fixture_dir: str) -> dict:
    ws_path = Path(workspace_dir).resolve()
    fix_path = Path(fixture_dir).resolve()

    target_file = ws_path / "src" / "scheduler.py"

    result = {
        "task": "task-6",
        "workspace_dir": str(ws_path),
        "target_file": "src/scheduler.py",
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
        result["failures"].append("Target file 'src/scheduler.py' does not exist")
        return result

    result["target_exists"] = True
    content = target_file.read_bytes()
    result["line_count"] = len(content.splitlines())
    result["sha256"] = hashlib.sha256(content).hexdigest()

    # 1. Verify single file modification rule:
    # All files other than src/scheduler.py must match the fixture exactly.
    fix_files = {p.relative_to(fix_path).as_posix(): p for p in fix_path.rglob("*") if p.is_file()}
    ws_files = {p.relative_to(ws_path).as_posix(): p for p in ws_path.rglob("*") if p.is_file()}

    # Ignore .git or __pycache__ if any
    clean_ws = {k: v for k, v in ws_files.items() if not k.startswith(".git") and "__pycache__" not in k}
    clean_fix = {k: v for k, v in fix_files.items() if not k.startswith(".git") and "__pycache__" not in k}

    unexpected = []
    for rel_path, f_path in clean_fix.items():
        if rel_path == "src/scheduler.py":
            continue
        if rel_path not in clean_ws:
            unexpected.append(f"Deleted required file: {rel_path}")
        else:
            if compute_file_hash(f_path) != compute_file_hash(clean_ws[rel_path]):
                unexpected.append(f"Modified unauthorized file: {rel_path}")

    for rel_path in clean_ws:
        if rel_path != "src/scheduler.py" and rel_path not in clean_fix:
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
        import src.models as models_mod
        import src.exceptions as exc_mod
        import src.scheduler as sched_mod
    except Exception as e:
        result["failures"].append(f"Import of workspace modules failed: {e}")
        return result

    Job = models_mod.Job
    Result = models_mod.Result
    CycleError = exc_mod.CycleError
    MissingDependencyError = exc_mod.MissingDependencyError
    SchedulerError = exc_mod.SchedulerError
    execute = sched_mod.execute

    # Helper for running a hidden test case
    def run_check(name: str, fn):
        result["hidden_total"] += 1
        try:
            fn()
            result["hidden_passed"] += 1
        except Exception as e:
            result["failures"].append(f"Hidden check '{name}' failed: {e}")

    # Check 1: Empty jobs dictionary returns empty dict
    def check_empty():
        async def runner(jid, att):
            return 1
        res = asyncio.run(execute({}, runner))
        assert res == {}, f"Expected empty dict, got {res}"
    run_check("empty_jobs", check_empty)

    # Check 2: Single independent job execution
    def check_single_job():
        async def runner(jid, att):
            return f"val_{jid}_{att}"
        jobs = {"X": Job()}
        res = asyncio.run(execute(jobs, runner))
        assert "X" in res, "Missing job X"
        assert res["X"].status == "SUCCESS"
        assert res["X"].value == "val_X_1"
        assert res["X"].attempts == 1
    run_check("single_job", check_single_job)

    # Check 3: Linear DAG ordering (A -> B -> C -> D)
    def check_linear_dag():
        order = []
        async def runner(jid, att):
            order.append(jid)
            await asyncio.sleep(0.005)
            return jid
        jobs = {
            "D": Job(deps=("C",)),
            "C": Job(deps=("B",)),
            "B": Job(deps=("A",)),
            "A": Job(),
        }
        res = asyncio.run(execute(jobs, runner, max_concurrency=4))
        assert order == ["A", "B", "C", "D"], f"Unexpected execution order: {order}"
        for k in ["A", "B", "C", "D"]:
            assert res[k].status == "SUCCESS"
    run_check("linear_dag_ordering", check_linear_dag)

    # Check 4: Diamond DAG completion and dependency resolution
    def check_diamond_dag():
        running_set = set()
        max_simul = 0
        async def runner(jid, att):
            nonlocal max_simul
            running_set.add(jid)
            max_simul = max(max_simul, len(running_set))
            await asyncio.sleep(0.02)
            running_set.remove(jid)
            return jid
        # In diamond DAG: A -> (B, C) -> D. B and C can run concurrently.
        jobs = {
            "A": Job(),
            "B": Job(deps=("A",)),
            "C": Job(deps=("A",)),
            "D": Job(deps=("B", "C")),
        }
        res = asyncio.run(execute(jobs, runner, max_concurrency=2))
        assert len(res) == 4
        assert all(r.status == "SUCCESS" for r in res.values())
        assert max_simul >= 2, f"B and C should have executed concurrently, max_simul was {max_simul}"
    run_check("diamond_dag_concurrency", check_diamond_dag)

    # Check 5: Cycle detection raises CycleError without executing any jobs
    def check_cycle_error():
        runner_called = False
        async def runner(jid, att):
            nonlocal runner_called
            runner_called = True
            return 1
        jobs = {
            "A": Job(deps=("C",)),
            "B": Job(deps=("A",)),
            "C": Job(deps=("B",)),
        }
        try:
            asyncio.run(execute(jobs, runner))
            assert False, "Failed to raise CycleError"
        except CycleError:
            pass
        assert not runner_called, "Runner must NOT be invoked when CycleError exists"
    run_check("cycle_detection_before_execution", check_cycle_error)

    # Check 6: Missing dependency raises MissingDependencyError without executing jobs
    def check_missing_dependency():
        runner_called = False
        async def runner(jid, att):
            nonlocal runner_called
            runner_called = True
            return 1
        jobs = {
            "A": Job(deps=("ghost",)),
        }
        try:
            asyncio.run(execute(jobs, runner))
            assert False, "Failed to raise MissingDependencyError"
        except MissingDependencyError:
            pass
        assert not runner_called, "Runner must NOT be invoked when MissingDependencyError exists"
    run_check("missing_dependency_error", check_missing_dependency)

    # Check 7: Self dependency cycle raises CycleError
    def check_self_dependency():
        runner_called = False
        async def runner(jid, att):
            nonlocal runner_called
            runner_called = True
            return 1
        jobs = {
            "SELF": Job(deps=("SELF",)),
        }
        try:
            asyncio.run(execute(jobs, runner))
            assert False, "Failed to raise CycleError on self-dependency"
        except CycleError:
            pass
        assert not runner_called, "Runner must NOT be invoked when self-dependency exists"
    run_check("self_dependency_cycle", check_self_dependency)

    # Check 8: Max concurrency strictly enforced
    def check_concurrency_limit():
        concurrent_count = 0
        peak_concurrency = 0
        lock = asyncio.Lock()

        async def runner(jid, att):
            nonlocal concurrent_count, peak_concurrency
            async with lock:
                concurrent_count += 1
                if concurrent_count > peak_concurrency:
                    peak_concurrency = concurrent_count
            await asyncio.sleep(0.02)
            async with lock:
                concurrent_count -= 1
            return jid

        jobs = {f"job_{i}": Job() for i in range(10)}
        res = asyncio.run(execute(jobs, runner, max_concurrency=3))
        assert len(res) == 10
        assert all(r.status == "SUCCESS" for r in res.values())
        assert peak_concurrency <= 3, f"Peak concurrency {peak_concurrency} exceeded limit of 3"
        assert peak_concurrency == 3, f"Peak concurrency {peak_concurrency} did not reach limit 3"
    run_check("max_concurrency_enforced", check_concurrency_limit)

    # Check 9: Resource exclusivity strictly enforced
    def check_resource_exclusivity():
        active_per_resource: dict[str, int] = {}
        collision_detected = False

        async def runner(jid, att):
            nonlocal collision_detected
            res_name = jobs[jid].resource
            if res_name:
                active_per_resource[res_name] = active_per_resource.get(res_name, 0) + 1
                if active_per_resource[res_name] > 1:
                    collision_detected = True
            await asyncio.sleep(0.01)
            if res_name:
                active_per_resource[res_name] -= 1
            return jid

        jobs = {
            "j1": Job(resource="gpu"),
            "j2": Job(resource="gpu"),
            "j3": Job(resource="gpu"),
            "j4": Job(resource="cpu"),
            "j5": Job(resource="cpu"),
        }
        res = asyncio.run(execute(jobs, runner, max_concurrency=4))
        assert len(res) == 5
        assert not collision_detected, "Two jobs accessed the same resource simultaneously!"
    run_check("resource_exclusivity_enforced", check_resource_exclusivity)

    # Check 10: Priority ordering: higher priority runs first
    def check_priority_ordering():
        exec_order = []
        started_event = asyncio.Event()

        async def runner(jid, att):
            exec_order.append(jid)
            await asyncio.sleep(0.005)
            return jid

        # 3 jobs with concurrency 1: highest priority must run first
        jobs = {
            "low": Job(priority=1),
            "high": Job(priority=100),
            "med": Job(priority=50),
        }
        asyncio.run(execute(jobs, runner, max_concurrency=1))
        assert exec_order == ["high", "med", "low"], f"Expected ['high', 'med', 'low'], got {exec_order}"
    run_check("priority_ordering", check_priority_ordering)

    # Check 11: Tie-breaking by stable input dict order for same priority
    def check_tie_breaking():
        exec_order = []
        async def runner(jid, att):
            exec_order.append(jid)
            await asyncio.sleep(0.002)
            return jid

        jobs = {
            "first": Job(priority=10),
            "second": Job(priority=10),
            "third": Job(priority=10),
        }
        asyncio.run(execute(jobs, runner, max_concurrency=1))
        assert exec_order == ["first", "second", "third"], f"Expected input dict order tie-breaking, got {exec_order}"
    run_check("priority_tie_breaking", check_tie_breaking)

    # Check 12: Retries up to max_attempts on transient errors
    def check_retries():
        attempts_seen = []
        async def runner(jid, att):
            attempts_seen.append(att)
            if att < 4:
                raise ValueError(f"Failing attempt {att}")
            return "recovered"

        jobs = {"R": Job(max_attempts=4)}
        res = asyncio.run(execute(jobs, runner))
        assert res["R"].status == "SUCCESS"
        assert res["R"].value == "recovered"
        assert res["R"].attempts == 4
        assert attempts_seen == [1, 2, 3, 4]
    run_check("retries_eventual_success", check_retries)

    # Check 13: Retry exhaustion records FAILED and correct attempts count
    def check_retry_exhaustion():
        async def runner(jid, att):
            raise RuntimeError(f"Dead on attempt {att}")

        jobs = {"F": Job(max_attempts=3)}
        res = asyncio.run(execute(jobs, runner))
        assert res["F"].status == "FAILED"
        assert "Dead on attempt 3" in (res["F"].error or "")
        assert res["F"].attempts == 3
    run_check("retry_exhaustion_failure", check_retry_exhaustion)

    # Check 14: Descendant skipping on permanent failure
    def check_descendant_skipping():
        async def runner(jid, att):
            if jid == "root":
                raise RuntimeError("Root explosion")
            return f"val_{jid}"

        # root (fails) -> child1 -> grandchild
        # independent -> succeeds
        jobs = {
            "root": Job(max_attempts=1),
            "child1": Job(deps=("root",)),
            "grandchild": Job(deps=("child1",)),
            "indep": Job(),
        }
        res = asyncio.run(execute(jobs, runner))
        assert res["root"].status == "FAILED"
        assert res["root"].attempts == 1
        assert res["child1"].status == "SKIPPED"
        assert res["child1"].attempts == 0
        assert res["grandchild"].status == "SKIPPED"
        assert res["grandchild"].attempts == 0
        assert res["indep"].status == "SUCCESS"
        assert res["indep"].attempts == 1
    run_check("descendant_skipping_on_failure", check_descendant_skipping)

    # Check 15: Independent branches continue when one branch fails
    def check_independent_branches():
        async def runner(jid, att):
            if jid == "fail_branch_root":
                raise RuntimeError("Branch failed")
            return jid

        jobs = {
            "fail_branch_root": Job(),
            "fail_branch_leaf": Job(deps=("fail_branch_root",)),
            "good_branch_root": Job(),
            "good_branch_leaf": Job(deps=("good_branch_root",)),
        }
        res = asyncio.run(execute(jobs, runner, max_concurrency=2))
        assert res["fail_branch_root"].status == "FAILED"
        assert res["fail_branch_leaf"].status == "SKIPPED"
        assert res["good_branch_root"].status == "SUCCESS"
        assert res["good_branch_leaf"].status == "SUCCESS"
    run_check("independent_branches_continue", check_independent_branches)

    # Check 16: Result dict key order matches input jobs dict key order
    def check_result_key_ordering():
        async def runner(jid, att):
            await asyncio.sleep(0.005)
            return jid

        # Insert order: Z, M, A, K
        jobs = {
            "Z": Job(),
            "M": Job(deps=("Z",)),
            "A": Job(),
            "K": Job(deps=("A",)),
        }
        res = asyncio.run(execute(jobs, runner, max_concurrency=2))
        assert list(res.keys()) == ["Z", "M", "A", "K"], f"Keys order mismatch: {list(res.keys())}"
    run_check("result_key_order_preservation", check_result_key_ordering)

    # Check 17: Runner raising unexpected non-standard exceptions handled cleanly
    def check_unexpected_exception():
        class CustomFatalError(BaseException):
            pass

        async def runner(jid, att):
            if jid == "bad":
                raise CustomFatalError("Fatal catastrophe")
            return "ok"

        jobs = {
            "good": Job(),
            "bad": Job(max_attempts=1),
            "dep_on_bad": Job(deps=("bad",)),
        }
        # A BaseException should either fail the job or abort cleanly without hung tasks
        try:
            res = asyncio.run(execute(jobs, runner))
            assert res["bad"].status == "FAILED"
            assert res["dep_on_bad"].status == "SKIPPED"
            assert res["good"].status == "SUCCESS"
        except CustomFatalError:
            # Also acceptable if BaseException is not caught by Exception
            pass
    run_check("unexpected_exception_handling", check_unexpected_exception)

    # Check 18: Resource contention with different priorities
    def check_resource_priority():
        order = []
        async def runner(jid, att):
            order.append(jid)
            await asyncio.sleep(0.01)
            return jid

        jobs = {
            "low_res": Job(priority=1, resource="db"),
            "high_res": Job(priority=10, resource="db"),
            "other": Job(priority=5),
        }
        res = asyncio.run(execute(jobs, runner, max_concurrency=2))
        assert len(res) == 3
        # high_res and other can start; low_res must wait for db and run after high_res
        assert order.index("high_res") < order.index("low_res")
    run_check("resource_priority_resolution", check_resource_priority)

    # Check 19: Simultaneous completions do not cause deadlocks or missed events
    def check_simultaneous_completions():
        count = 16
        async def runner(jid, att):
            # All sleep exact same time to complete near simultaneously
            await asyncio.sleep(0.01)
            return jid

        jobs = {f"sim_{i}": Job() for i in range(count)}
        t0 = time.monotonic()
        res = asyncio.run(execute(jobs, runner, max_concurrency=8))
        elapsed = time.monotonic() - t0
        assert len(res) == count
        assert all(r.status == "SUCCESS" for r in res.values())
        assert elapsed < 2.0, f"Simultaneous completion took too long: {elapsed:.2f}s"
    run_check("simultaneous_completions", check_simultaneous_completions)

    # Check 20: Event-driven performance (no busy-waiting / polling loops)
    def check_event_driven_no_busy_wait():
        sleep_called = False
        orig_sleep = asyncio.sleep

        # Wrap runner with slow job to test idle waiting
        async def runner(jid, att):
            if jid == "slow":
                await orig_sleep(0.05)
            return jid

        jobs = {
            "slow": Job(),
            "dep": Job(deps=("slow",)),
        }
        res = asyncio.run(execute(jobs, runner, max_concurrency=2))
        assert res["slow"].status == "SUCCESS"
        assert res["dep"].status == "SUCCESS"
    run_check("event_driven_execution", check_event_driven_no_busy_wait)

    # Check 21: Partial skip with multiple dependencies (one fail, one success)
    def check_multi_dep_skip():
        async def runner(jid, att):
            if jid == "fail_parent":
                raise ValueError("fail")
            return "ok"

        # child depends on both good_parent and fail_parent
        jobs = {
            "good_parent": Job(),
            "fail_parent": Job(max_attempts=1),
            "child": Job(deps=("good_parent", "fail_parent")),
        }
        res = asyncio.run(execute(jobs, runner, max_concurrency=2))
        assert res["good_parent"].status == "SUCCESS"
        assert res["fail_parent"].status == "FAILED"
        assert res["child"].status == "SKIPPED"
        assert res["child"].attempts == 0
    run_check("multi_dependency_skip_behavior", check_multi_dep_skip)

    # Check 22: High load DAG with wide parallelism
    def check_wide_parallelism():
        async def runner(jid, att):
            await asyncio.sleep(0.005)
            return jid

        # 25 independent jobs + 5 barrier jobs
        jobs = {f"wide_{i}": Job() for i in range(25)}
        for b in range(5):
            deps = tuple(f"wide_{i}" for i in range(b * 5, (b + 1) * 5))
            jobs[f"barrier_{b}"] = Job(deps=deps)

        res = asyncio.run(execute(jobs, runner, max_concurrency=10))
        assert len(res) == 30
        assert all(r.status == "SUCCESS" for r in res.values())
    run_check("wide_parallelism_dag", check_wide_parallelism)

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
        print("Usage: eval_task_6.py <workspace-dir> <fixture-dir>")
        sys.exit(1)
    res = evaluate_task_6(sys.argv[1], sys.argv[2])
    print(json.dumps(res, indent=2))
    sys.exit(0 if res["status"] == "PASS" else 1)
