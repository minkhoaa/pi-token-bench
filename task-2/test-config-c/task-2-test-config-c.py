import asyncio
import sys
import unittest
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
    if (
        isinstance(max_concurrency, bool)
        or not isinstance(max_concurrency, int)
        or max_concurrency <= 0
    ):
        raise ValueError("max_concurrency must be an int greater than zero")
    if not callable(worker):
        raise ValueError("worker must be callable")
    if not isinstance(jobs, dict):
        raise ValueError("jobs must be a dict")

    for job_id, job in jobs.items():
        if not isinstance(job_id, str) or not job_id:
            raise ValueError(f"invalid job ID: {job_id!r}")
        if not isinstance(job, Job):
            raise ValueError(f"job {job_id!r} must be a Job")
        if not isinstance(job.deps, tuple):
            raise ValueError(f"dependencies for {job_id!r} must be a tuple")
        if any(not isinstance(dep, str) or not dep for dep in job.deps):
            raise ValueError(f"invalid dependency ID in job {job_id!r}")
        if len(set(job.deps)) != len(job.deps):
            raise ValueError(f"duplicate dependency in job {job_id!r}")
        for dep in job.deps:
            if dep not in jobs:
                raise ValueError(f"job {job_id!r} depends on missing job {dep!r}")
            if dep == job_id:
                raise ValueError(f"job {job_id!r} depends on itself")

    # Kahn's algorithm validates the full graph before any worker is started.
    indegree = {job_id: len(job.deps) for job_id, job in jobs.items()}
    dependents = {job_id: [] for job_id in jobs}
    ready = [job_id for job_id, degree in indegree.items() if degree == 0]
    for job_id, job in jobs.items():
        for dep in job.deps:
            dependents[dep].append(job_id)
    visited = 0
    while ready:
        job_id = ready.pop()
        visited += 1
        for dependent in dependents[job_id]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
    if visited != len(jobs):
        raise ValueError("job graph contains a dependency cycle")

    if not jobs:
        return {}

    async def execute(job_id: str) -> JobResult:
        try:
            value = await worker(job_id)
            return JobResult(status="completed", value=value, error=None)
        except Exception as exc:
            return JobResult(
                status="failed",
                value=None,
                error=f"{type(exc).__name__}: {exc}",
            )

    pending = set(jobs)
    results: dict[str, JobResult] = {}
    active: dict[asyncio.Task[JobResult], str] = {}

    try:
        while pending or active:
            # Launch newly ready jobs in input order, and mark blocked descendants.
            made_progress = True
            while len(active) < max_concurrency and made_progress:
                made_progress = False
                for job_id, job in jobs.items():
                    if job_id not in pending:
                        continue
                    dep_results = [results.get(dep) for dep in job.deps]
                    if any(result is not None and result.status != "completed" for result in dep_results):
                        results[job_id] = JobResult(status="skipped")
                        pending.remove(job_id)
                        made_progress = True
                        continue
                    if all(result is not None for result in dep_results):
                        pending.remove(job_id)
                        active[asyncio.create_task(execute(job_id))] = job_id
                        made_progress = True
                        if len(active) >= max_concurrency:
                            break

            if active:
                done, _ = await asyncio.wait(
                    active, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    job_id = active.pop(task)
                    results[job_id] = task.result()
            elif pending:
                # The validated DAG must always have a runnable or newly skipped job.
                raise RuntimeError("scheduler made no progress")

        return {job_id: results[job_id] for job_id in jobs}
    except asyncio.CancelledError:
        tasks = tuple(active)
        for task in tasks:
            task.cancel()
        if tasks:
            cleanup = asyncio.gather(*tasks, return_exceptions=True)
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    # Finish cleanup even if the caller issues another cancellation.
                    continue
        raise


class RunDagTests(unittest.IsolatedAsyncioTestCase):
    async def test_dependency_ordering(self) -> None:
        prerequisite_done = asyncio.Event()
        calls: list[str] = []

        async def worker(job_id: str) -> str:
            calls.append(job_id)
            if job_id == "dependent":
                self.assertTrue(prerequisite_done.is_set())
            else:
                prerequisite_done.set()
            return job_id

        jobs = {"dependent": Job(("prerequisite",)), "prerequisite": Job()}
        results = await asyncio.wait_for(run_dag(jobs, worker), timeout=2)
        self.assertEqual(results["dependent"].status, "completed")
        self.assertEqual(calls, ["prerequisite", "dependent"])

    async def test_concurrency_limit_enforcement(self) -> None:
        active = 0
        peak = 0
        two_started = asyncio.Event()
        release = asyncio.Event()

        async def worker(job_id: str) -> str:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 2:
                two_started.set()
            try:
                await release.wait()
                return job_id
            finally:
                active -= 1

        jobs = {name: Job() for name in ("a", "b", "c", "d")}
        task = asyncio.create_task(run_dag(jobs, worker, max_concurrency=2))
        try:
            await asyncio.wait_for(two_started.wait(), timeout=2)
            self.assertEqual(active, 2)
            self.assertLessEqual(peak, 2)
        finally:
            release.set()
        results = await asyncio.wait_for(task, timeout=2)
        self.assertEqual(len(results), 4)
        self.assertLessEqual(peak, 2)

    async def test_failure_propagation(self) -> None:
        calls: list[str] = []

        async def worker(job_id: str) -> str:
            calls.append(job_id)
            raise ValueError("boom")

        jobs = {
            "root": Job(),
            "child": Job(("root",)),
            "grandchild": Job(("child",)),
        }
        results = await asyncio.wait_for(run_dag(jobs, worker), timeout=2)
        self.assertEqual(results["root"], JobResult("failed", error="ValueError: boom"))
        self.assertEqual(results["child"], JobResult("skipped"))
        self.assertEqual(results["grandchild"], JobResult("skipped"))
        self.assertEqual(calls, ["root"])

    async def test_independent_branches_continue_after_failure(self) -> None:
        calls: list[str] = []

        async def worker(job_id: str) -> str:
            calls.append(job_id)
            if job_id == "broken":
                raise RuntimeError("failure")
            return "ok"

        jobs = {
            "broken": Job(),
            "independent": Job(),
            "downstream": Job(("broken",)),
        }
        results = await asyncio.wait_for(run_dag(jobs, worker), timeout=2)
        self.assertEqual(results["broken"].status, "failed")
        self.assertEqual(results["independent"], JobResult("completed", "ok"))
        self.assertEqual(results["downstream"].status, "skipped")
        self.assertIn("independent", calls)
        self.assertNotIn("downstream", calls)

    async def test_diamond_graph_execution(self) -> None:
        finished: set[str] = set()
        calls: dict[str, int] = {}

        async def worker(job_id: str) -> str:
            calls[job_id] = calls.get(job_id, 0) + 1
            if job_id in ("B", "C"):
                self.assertIn("A", finished)
            if job_id == "D":
                self.assertTrue({"B", "C"}.issubset(finished))
            finished.add(job_id)
            return job_id

        jobs = {
            "A": Job(),
            "B": Job(("A",)),
            "C": Job(("A",)),
            "D": Job(("B", "C")),
        }
        results = await asyncio.wait_for(
            run_dag(jobs, worker, max_concurrency=2), timeout=2
        )
        self.assertTrue(all(result.status == "completed" for result in results.values()))
        self.assertEqual(calls, {"A": 1, "B": 1, "C": 1, "D": 1})

    async def test_graph_validation(self) -> None:
        async def worker(job_id: str) -> None:
            self.fail("worker must not run for an invalid graph")

        invalid_cases = [
            ({"a": Job(("b",)), "b": Job(("a",))}, 3, worker),
            ({"a": Job(("a",))}, 3, worker),
            ({"a": Job(("missing",))}, 3, worker),
            ({"a": Job()}, 0, worker),
            ({"a": Job()}, True, worker),
            ({"a": Job()}, 1.5, worker),
            ({"": Job()}, 3, worker),
            ({1: Job()}, 3, worker),
            ({"a": object()}, 3, worker),
            ({"a": Job(["b"])}, 3, worker),
            ({"a": Job(("b", "b")), "b": Job()}, 3, worker),
            ({"a": Job()}, 3, None),
        ]
        for jobs, concurrency, candidate in invalid_cases:
            with self.subTest(jobs=jobs, concurrency=concurrency, worker=candidate):
                with self.assertRaises(ValueError):
                    await asyncio.wait_for(
                        run_dag(jobs, candidate, concurrency), timeout=2  # type: ignore[arg-type]
                    )

    async def test_stable_return_order(self) -> None:
        jobs = {
            "last": Job(("middle",)),
            "first": Job(),
            "middle": Job(("first",)),
        }

        async def worker(job_id: str) -> str:
            return job_id

        results = await asyncio.wait_for(run_dag(jobs, worker), timeout=2)
        self.assertEqual(list(results), list(jobs))

    async def test_cancellation_cleanup(self) -> None:
        active = 0
        finished = 0
        calls: list[str] = []
        worker_tasks: list[asyncio.Task[Any]] = []
        two_started = asyncio.Event()
        never_release = asyncio.Event()

        async def worker(job_id: str) -> None:
            nonlocal active, finished
            calls.append(job_id)
            active += 1
            current = asyncio.current_task()
            assert current is not None
            worker_tasks.append(current)
            if active == 2:
                two_started.set()
            try:
                await never_release.wait()
            finally:
                active -= 1
                finished += 1

        runner = asyncio.create_task(
            run_dag({"a": Job(), "b": Job(), "c": Job()}, worker, max_concurrency=2)
        )
        await asyncio.wait_for(two_started.wait(), timeout=2)
        runner.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(runner, timeout=2)

        self.assertEqual(active, 0)
        self.assertEqual(finished, 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(worker_tasks), 2)
        self.assertTrue(all(task.done() for task in worker_tasks))
        self.assertTrue(all(task not in asyncio.all_tasks() for task in worker_tasks))

    async def test_empty_graph(self) -> None:
        async def worker(job_id: str) -> None:
            self.fail("worker must not run for an empty graph")

        self.assertEqual(await asyncio.wait_for(run_dag({}, worker), timeout=2), {})


if __name__ == "__main__" and "--self-test" in sys.argv[1:]:
    unittest.main(argv=[sys.argv[0]])
