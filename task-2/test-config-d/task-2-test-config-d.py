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
    if isinstance(jobs, dict) and not jobs:
        return {}
    if not isinstance(jobs, dict):
        raise ValueError("jobs must be a dict")
    if (
        not isinstance(max_concurrency, int)
        or isinstance(max_concurrency, bool)
        or max_concurrency <= 0
    ):
        raise ValueError("max_concurrency must be a positive integer")
    if not callable(worker):
        raise ValueError("worker must be callable")

    job_ids = list(jobs)
    for job_id in job_ids:
        if not isinstance(job_id, str) or not job_id:
            raise ValueError(f"invalid job ID: {job_id!r}")

    dependents = {job_id: [] for job_id in job_ids}
    indegree = {}
    for job_id, job in jobs.items():
        if not isinstance(job, Job):
            raise ValueError(f"job {job_id!r} must be a Job")
        if not isinstance(job.deps, tuple):
            raise ValueError(f"dependencies for {job_id!r} must be a tuple")
        seen_deps = set()
        for dep in job.deps:
            if not isinstance(dep, str) or not dep:
                raise ValueError(f"job {job_id!r} has invalid dependency {dep!r}")
            if dep in seen_deps:
                raise ValueError(f"job {job_id!r} has duplicate dependencies")
            seen_deps.add(dep)
            if dep not in jobs:
                raise ValueError(f"job {job_id!r} has missing dependency {dep!r}")
            if dep == job_id:
                raise ValueError(f"job {job_id!r} depends on itself")
            dependents[dep].append(job_id)
        indegree[job_id] = len(job.deps)

    ready = deque(job_id for job_id in job_ids if indegree[job_id] == 0)
    visited = 0
    while ready:
        job_id = ready.popleft()
        visited += 1
        for child in dependents[job_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if visited != len(job_ids):
        raise ValueError("dependency cycle detected")

    async def invoke(job_id: str) -> JobResult:
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
    pending = list(job_ids)
    active: dict[str, asyncio.Task[JobResult]] = {}
    try:
        while pending or active:
            # Propagate skips to a fixed point before starting more workers.
            changed = True
            while changed:
                changed = False
                for job_id in tuple(pending):
                    if any(
                        dep in results and results[dep].status != "completed"
                        for dep in jobs[job_id].deps
                    ):
                        results[job_id] = JobResult(status="skipped")
                        pending.remove(job_id)
                        changed = True

            for job_id in tuple(pending):
                if len(active) >= max_concurrency:
                    break
                if all(
                    dep in results and results[dep].status == "completed"
                    for dep in jobs[job_id].deps
                ):
                    active[job_id] = asyncio.create_task(
                        invoke(job_id), name=f"run_dag:{job_id}"
                    )
                    pending.remove(job_id)

            if active:
                done, _ = await asyncio.wait(
                    active.values(), return_when=asyncio.FIRST_COMPLETED
                )
                for job_id, task in tuple(active.items()):
                    if task in done:
                        del active[job_id]
                        try:
                            results[job_id] = task.result()
                        except asyncio.CancelledError as exc:
                            results[job_id] = JobResult(
                                status="failed",
                                error=f"{type(exc).__name__}: {exc}",
                            )
            elif pending:
                # Validation makes this unreachable unless scheduler state is corrupted.
                raise RuntimeError("DAG scheduler made no progress")
    except asyncio.CancelledError:
        for task in active.values():
            task.cancel()
        if active:
            await asyncio.gather(*active.values(), return_exceptions=True)
        raise

    return {job_id: results[job_id] for job_id in job_ids}


class RunDagTests(unittest.IsolatedAsyncioTestCase):
    async def test_dependency_ordering(self):
        async with asyncio.timeout(3):
            root_started = asyncio.Event()
            release_root = asyncio.Event()
            child_started = asyncio.Event()

            async def worker(job_id):
                if job_id == "root":
                    root_started.set()
                    await release_root.wait()
                else:
                    child_started.set()
                return job_id

            task = asyncio.create_task(
                run_dag({"root": Job(), "child": Job(("root",))}, worker)
            )
            try:
                await root_started.wait()
                self.assertFalse(child_started.is_set())
            finally:
                release_root.set()
            results = await task
            self.assertTrue(child_started.is_set())
            self.assertEqual(results["child"].status, "completed")

    async def test_concurrency_limit_enforcement(self):
        async with asyncio.timeout(3):
            active = 0
            peak = 0
            started = 0
            enough_started = asyncio.Event()
            release = asyncio.Event()

            async def worker(_job_id):
                nonlocal active, peak, started
                active += 1
                started += 1
                peak = max(peak, active)
                if started >= 2:
                    enough_started.set()
                try:
                    await release.wait()
                finally:
                    active -= 1

            task = asyncio.create_task(
                run_dag({str(i): Job() for i in range(7)}, worker, max_concurrency=2)
            )
            await enough_started.wait()
            self.assertEqual(active, 2)
            release.set()
            results = await task
            self.assertEqual(len(results), 7)
            self.assertEqual(peak, 2)

    async def test_failure_propagation(self):
        async with asyncio.timeout(3):
            calls = []

            async def worker(job_id):
                calls.append(job_id)
                if job_id == "bad":
                    raise RuntimeError("boom")
                return job_id

            results = await run_dag(
                {"bad": Job(), "child": Job(("bad",)), "leaf": Job(("child",))},
                worker,
            )
            self.assertEqual(calls, ["bad"])
            self.assertEqual(results["bad"].status, "failed")
            self.assertEqual(results["bad"].error, "RuntimeError: boom")
            self.assertEqual(results["child"].status, "skipped")
            self.assertEqual(results["leaf"].status, "skipped")

    async def test_independent_branches_continue_after_failure(self):
        async with asyncio.timeout(3):
            calls = []
            independent_finished = asyncio.Event()

            async def worker(job_id):
                calls.append(job_id)
                if job_id == "bad":
                    raise ValueError("failed branch")
                if job_id == "independent":
                    independent_finished.set()
                return job_id

            results = await run_dag(
                {
                    "bad": Job(),
                    "descendant": Job(("bad",)),
                    "independent": Job(),
                },
                worker,
            )
            self.assertTrue(independent_finished.is_set())
            self.assertIn("independent", calls)
            self.assertNotIn("descendant", calls)
            self.assertEqual(results["descendant"].status, "skipped")
            self.assertEqual(results["independent"].status, "completed")

    async def test_diamond_graph_execution(self):
        async with asyncio.timeout(3):
            finished = {job_id: asyncio.Event() for job_id in "ABCD"}
            calls = []

            async def worker(job_id):
                calls.append(job_id)
                deps = {"A": (), "B": ("A",), "C": ("A",), "D": ("B", "C")}
                for dep in deps[job_id]:
                    self.assertTrue(finished[dep].is_set())
                finished[job_id].set()
                return job_id

            results = await run_dag(
                {"A": Job(), "B": Job(("A",)), "C": Job(("A",)), "D": Job(("B", "C"))},
                worker,
            )
            self.assertEqual(set(calls), set("ABCD"))
            self.assertEqual(calls[0], "A")
            self.assertEqual(calls[-1], "D")
            self.assertTrue(all(result.status == "completed" for result in results.values()))

    async def test_graph_validation(self):
        async with asyncio.timeout(3):
            called = False

            async def worker(_job_id):
                nonlocal called
                called = True

            invalid_cases = [
                ({"ready": Job(), "a": Job(("b",)), "b": Job(("a",))}, worker, 3),
                ({"self": Job(("self",))}, worker, 3),
                ({"a": Job(("missing",))}, worker, 3),
                ({"a": Job()}, worker, 0),
                ({"a": Job()}, worker, True),
                ({"a": object()}, worker, 3),
                ({"a": Job([])}, worker, 3),
                ({"a": Job(("a", "a"))}, worker, 3),
                ({"": Job()}, worker, 3),
                ({1: Job()}, worker, 3),
                ({"a": Job()}, None, 3),
                ({"a": Job((None,))}, worker, 3),
            ]
            for jobs, candidate, concurrency in invalid_cases:
                with self.subTest(jobs=jobs, concurrency=concurrency):
                    with self.assertRaises(ValueError):
                        await run_dag(jobs, candidate, concurrency)
            self.assertFalse(called)

    async def test_stable_return_order(self):
        async with asyncio.timeout(3):
            jobs = {"third": Job(), "first": Job(), "second": Job()}

            async def worker(job_id):
                return job_id

            results = await run_dag(jobs, worker)
            self.assertEqual(list(results), list(jobs))

    async def test_cancellation_cleanup(self):
        async with asyncio.timeout(3):
            started = {job_id: asyncio.Event() for job_id in ("a", "b")}
            cleaned = {job_id: asyncio.Event() for job_id in ("a", "b")}
            worker_tasks = []

            async def worker(job_id):
                worker_tasks.append(asyncio.current_task())
                started[job_id].set()
                try:
                    await asyncio.Future()
                finally:
                    cleaned[job_id].set()

            runner = asyncio.create_task(
                run_dag({"a": Job(), "b": Job(), "c": Job()}, worker, 2)
            )
            await asyncio.gather(*(event.wait() for event in started.values()))
            runner.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(runner, timeout=1)
            self.assertTrue(all(event.is_set() for event in cleaned.values()))
            self.assertTrue(all(task.done() for task in worker_tasks))

    async def test_empty_graph(self):
        async with asyncio.timeout(3):
            self.assertEqual(await run_dag({}, None, 0), {})


if __name__ == "__main__" and sys.argv[1:] == ["--self-test"]:
    unittest.main(argv=[sys.argv[0]])
