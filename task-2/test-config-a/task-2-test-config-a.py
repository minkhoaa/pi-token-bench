from __future__ import annotations

import asyncio
import sys
import unittest
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class Job:
    deps: tuple[str, ...] = ()


@dataclass(frozen=True)
class JobResult:
    status: str
    value: Any = None
    error: str | None = None


async def run_dag(
    jobs: dict[str, Job],
    worker: Callable[[str], Awaitable[Any]],
    max_concurrency: int = 3,
) -> dict[str, JobResult]:
    """Run a validated dependency graph, skipping jobs blocked by failures."""
    if not isinstance(jobs, dict):
        raise ValueError("jobs must be a dict")
    if (
        isinstance(max_concurrency, bool)
        or not isinstance(max_concurrency, int)
        or max_concurrency <= 0
    ):
        raise ValueError("max_concurrency must be an int greater than zero")
    if not callable(worker):
        raise ValueError("worker must be callable")

    job_ids = tuple(jobs)
    for job_id in job_ids:
        if not isinstance(job_id, str) or not job_id:
            raise ValueError(f"invalid job ID: {job_id!r}")

    if not jobs:
        return {}

    for job_id, job in jobs.items():
        if not isinstance(job, Job):
            raise ValueError(f"job {job_id!r} is not a Job")
        if not isinstance(job.deps, tuple):
            raise ValueError(f"dependencies for job {job_id!r} must be a tuple")
        if any(not isinstance(dep, str) or not dep for dep in job.deps):
            raise ValueError(f"job {job_id!r} has an invalid dependency ID")
        if len(set(job.deps)) != len(job.deps):
            raise ValueError(f"job {job_id!r} has duplicate dependencies")
        if job_id in job.deps:
            raise ValueError(f"job {job_id!r} depends on itself")
        for dep in job.deps:
            if dep not in jobs:
                raise ValueError(f"job {job_id!r} depends on missing job {dep!r}")

    # Kahn's algorithm validates acyclicity before any worker can be started.
    remaining_deps = {job_id: len(jobs[job_id].deps) for job_id in job_ids}
    dependents: dict[str, list[str]] = {job_id: [] for job_id in job_ids}
    for job_id in job_ids:
        for dep in jobs[job_id].deps:
            dependents[dep].append(job_id)
    ready = deque(job_id for job_id in job_ids if remaining_deps[job_id] == 0)
    visited = 0
    while ready:
        job_id = ready.popleft()
        visited += 1
        for dependent in dependents[job_id]:
            remaining_deps[dependent] -= 1
            if remaining_deps[dependent] == 0:
                ready.append(dependent)
    if visited != len(job_ids):
        raise ValueError("dependency graph contains a cycle")

    async def execute(job_id: str) -> JobResult:
        try:
            value = await worker(job_id)
        except Exception as exc:
            return JobResult(
                status="failed",
                value=None,
                error=f"{type(exc).__name__}: {exc}",
            )
        return JobResult(status="completed", value=value, error=None)

    results: dict[str, JobResult] = {}
    pending = set(job_ids)
    active: dict[asyncio.Task[JobResult], str] = {}

    try:
        while pending or active:
            # Propagate skipped status through descendants before scheduling.
            changed = True
            while changed:
                changed = False
                for job_id in job_ids:
                    if job_id not in pending:
                        continue
                    if any(
                        dep in results and results[dep].status != "completed"
                        for dep in jobs[job_id].deps
                    ):
                        results[job_id] = JobResult(status="skipped")
                        pending.remove(job_id)
                        changed = True

            # Scanning in input order makes launch order deterministic while
            # allowing all independent ready jobs to start up to the limit.
            for job_id in job_ids:
                if len(active) >= max_concurrency:
                    break
                if job_id not in pending:
                    continue
                if all(dep in results for dep in jobs[job_id].deps):
                    task = asyncio.create_task(execute(job_id))
                    pending.remove(job_id)
                    active[task] = job_id

            if not active:
                if pending:  # Defensive: a validated DAG cannot reach this state.
                    raise RuntimeError("scheduler stalled with pending jobs")
                break

            done, _ = await asyncio.wait(
                active, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                job_id = active.pop(task)
                results[job_id] = task.result()
    except asyncio.CancelledError:
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        raise

    return {job_id: results[job_id] for job_id in job_ids}


class RunDagTests(unittest.IsolatedAsyncioTestCase):
    async def test_dependency_ordering(self) -> None:
        root_started = asyncio.Event()
        release_root = asyncio.Event()
        child_started = asyncio.Event()

        async def worker(job_id: str) -> str:
            if job_id == "root":
                root_started.set()
                await release_root.wait()
            else:
                child_started.set()
            return job_id

        task = asyncio.create_task(
            run_dag({"child": Job(("root",)), "root": Job()}, worker)
        )
        try:
            await asyncio.wait_for(root_started.wait(), 1)
            self.assertFalse(child_started.is_set())
            release_root.set()
            results = await asyncio.wait_for(task, 1)
            self.assertTrue(child_started.is_set())
            self.assertEqual(results["child"].status, "completed")
        finally:
            release_root.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_concurrency_limit_enforcement(self) -> None:
        active = 0
        maximum = 0
        two_started = asyncio.Event()
        release = asyncio.Event()

        async def worker(job_id: str) -> str:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                two_started.set()
            try:
                await release.wait()
                return job_id
            finally:
                active -= 1

        task = asyncio.create_task(
            run_dag({str(i): Job() for i in range(5)}, worker, max_concurrency=2)
        )
        try:
            await asyncio.wait_for(two_started.wait(), 1)
            self.assertEqual(active, 2)
            self.assertLessEqual(maximum, 2)
            release.set()
            results = await asyncio.wait_for(task, 1)
            self.assertEqual(len(results), 5)
            self.assertLessEqual(maximum, 2)
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_failure_propagation(self) -> None:
        called: list[str] = []

        async def worker(job_id: str) -> str:
            called.append(job_id)
            if job_id == "bad":
                raise RuntimeError("broken")
            return job_id

        results = await asyncio.wait_for(
            run_dag(
                {
                    "grandchild": Job(("child",)),
                    "child": Job(("bad",)),
                    "bad": Job(),
                },
                worker,
            ),
            1,
        )
        self.assertEqual(called, ["bad"])
        self.assertEqual(results["bad"], JobResult("failed", error="RuntimeError: broken"))
        self.assertEqual(results["child"], JobResult("skipped"))
        self.assertEqual(results["grandchild"], JobResult("skipped"))

    async def test_independent_branches_continue_after_failure(self) -> None:
        called: set[str] = set()

        async def worker(job_id: str) -> str:
            called.add(job_id)
            if job_id == "bad":
                raise ValueError("nope")
            return job_id

        results = await asyncio.wait_for(
            run_dag(
                {"bad": Job(), "good": Job(), "good_child": Job(("good",))},
                worker,
                max_concurrency=2,
            ),
            1,
        )
        self.assertEqual(called, {"bad", "good", "good_child"})
        self.assertEqual(results["bad"].status, "failed")
        self.assertEqual(results["good_child"].status, "completed")

    async def test_diamond_graph_execution(self) -> None:
        branch_b_started = asyncio.Event()
        branch_c_started = asyncio.Event()
        release_branches = asyncio.Event()
        destination_started = asyncio.Event()
        called: list[str] = []

        async def worker(job_id: str) -> str:
            called.append(job_id)
            if job_id in ("B", "C"):
                (branch_b_started if job_id == "B" else branch_c_started).set()
                await release_branches.wait()
            elif job_id == "D":
                destination_started.set()
            return job_id

        task = asyncio.create_task(
            run_dag(
                {"A": Job(), "B": Job(("A",)), "C": Job(("A",)), "D": Job(("B", "C"))},
                worker,
                max_concurrency=3,
            )
        )
        try:
            await asyncio.wait_for(branch_b_started.wait(), 1)
            await asyncio.wait_for(branch_c_started.wait(), 1)
            self.assertFalse(destination_started.is_set())
            release_branches.set()
            results = await asyncio.wait_for(task, 1)
            self.assertTrue(destination_started.is_set())
            self.assertEqual(called.count("D"), 1)
            self.assertTrue(all(result.status == "completed" for result in results.values()))
        finally:
            release_branches.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_graph_validation_precedes_worker_invocation(self) -> None:
        calls: list[str] = []

        async def worker(job_id: str) -> None:
            calls.append(job_id)

        invalid_graphs = [
            {"A": Job(("B",)), "B": Job(("A",))},  # cycle
            {"A": Job(("A",))},  # self-dependency
            {"A": Job(("missing",))},  # missing dependency
            {"A": Job(deps=["B"])},  # deps must be a tuple
            {"A": Job(("B", "B")), "B": Job()},  # duplicate dependency
            {1: Job()},  # invalid job ID
            {"A": object()},  # invalid job object
        ]
        for graph in invalid_graphs:
            with self.subTest(graph=graph):
                with self.assertRaises(ValueError):
                    await run_dag(graph, worker)  # type: ignore[arg-type]
                self.assertEqual(calls, [])

        for concurrency in (0, -1, True, 1.5):
            with self.subTest(concurrency=concurrency):
                with self.assertRaises(ValueError):
                    await run_dag({"A": Job()}, worker, concurrency)  # type: ignore[arg-type]
                self.assertEqual(calls, [])

        with self.assertRaises(ValueError):
            await run_dag({"A": Job()}, None)  # type: ignore[arg-type]
        self.assertEqual(calls, [])

    async def test_stable_return_order(self) -> None:
        slow_started = asyncio.Event()
        fast_finished = asyncio.Event()
        release_slow = asyncio.Event()

        async def worker(job_id: str) -> str:
            if job_id == "slow":
                slow_started.set()
                await release_slow.wait()
            else:
                fast_finished.set()
            return job_id

        task = asyncio.create_task(
            run_dag({"slow": Job(), "fast": Job()}, worker, max_concurrency=2)
        )
        try:
            await asyncio.wait_for(slow_started.wait(), 1)
            await asyncio.wait_for(fast_finished.wait(), 1)
            self.assertFalse(task.done())
            release_slow.set()
            results = await asyncio.wait_for(task, 1)
            self.assertEqual(list(results), ["slow", "fast"])
        finally:
            release_slow.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_cancellation_cleanup(self) -> None:
        started = 0
        cleaned_up = 0
        all_started = asyncio.Event()
        all_cleaned_up = asyncio.Event()
        child_tasks: set[asyncio.Task[Any]] = set()
        never_release = asyncio.Event()

        async def worker(job_id: str) -> None:
            nonlocal started, cleaned_up
            started += 1
            child_tasks.add(asyncio.current_task())  # type: ignore[arg-type]
            if started == 2:
                all_started.set()
            try:
                await never_release.wait()
            finally:
                cleaned_up += 1
                if cleaned_up == 2:
                    all_cleaned_up.set()

        task = asyncio.create_task(
            run_dag({"one": Job(), "two": Job()}, worker, max_concurrency=2)
        )
        await asyncio.wait_for(all_started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        await asyncio.wait_for(all_cleaned_up.wait(), 1)
        self.assertEqual(started, 2)
        self.assertEqual(cleaned_up, 2)
        self.assertTrue(all(child.done() for child in child_tasks))
        self.assertTrue(all(child.cancelled() for child in child_tasks))

    async def test_empty_graph(self) -> None:
        async def worker(job_id: str) -> str:
            self.fail("worker must not run for an empty graph")

        results = await asyncio.wait_for(run_dag({}, worker), 1)
        self.assertEqual(results, {})


if __name__ == "__main__" and "--self-test" in sys.argv:
    unittest.main(argv=[sys.argv[0]], verbosity=2)
