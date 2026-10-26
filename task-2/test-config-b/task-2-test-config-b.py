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
    if isinstance(max_concurrency, bool) or not isinstance(max_concurrency, int) or max_concurrency <= 0:
        raise ValueError("max_concurrency must be a positive integer")
    if not callable(worker):
        raise ValueError("worker must be callable")
    if not isinstance(jobs, dict):
        raise ValueError("jobs must be a dict")

    job_ids = list(jobs)
    for job_id in job_ids:
        if not isinstance(job_id, str) or not job_id:
            raise ValueError(f"invalid job ID: {job_id!r}")

    successors = {job_id: [] for job_id in job_ids}
    indegree = {}
    for job_id, job in jobs.items():
        if not isinstance(job, Job):
            raise ValueError(f"job {job_id!r} must be a Job")
        if not isinstance(job.deps, tuple):
            raise ValueError(f"dependencies for {job_id!r} must be a tuple")
        for dep in job.deps:
            if not isinstance(dep, str) or not dep:
                raise ValueError(f"invalid dependency ID in job {job_id!r}: {dep!r}")
        if len(set(job.deps)) != len(job.deps):
            raise ValueError(f"duplicate dependencies for {job_id!r}")
        for dep in job.deps:
            if dep not in jobs:
                raise ValueError(f"job {job_id!r} depends on missing job {dep!r}")
            if dep == job_id:
                raise ValueError(f"job {job_id!r} depends on itself")
            successors[dep].append(job_id)
        indegree[job_id] = len(job.deps)

    ready = deque(job_id for job_id in job_ids if indegree[job_id] == 0)
    visited = 0
    while ready:
        job_id = ready.popleft()
        visited += 1
        for child in successors[job_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if visited != len(job_ids):
        raise ValueError("dependency cycle detected")

    if not jobs:
        return {}

    results: dict[str, JobResult] = {}
    pending = set(job_ids)
    active: dict[asyncio.Task[Any], str] = {}

    async def invoke(job_id: str) -> Any:
        return await worker(job_id)

    try:
        while pending or active:
            progress = False
            for job_id in job_ids:
                if job_id not in pending:
                    continue
                deps = jobs[job_id].deps
                if any(
                    dep in results and results[dep].status != "completed" for dep in deps
                ):
                    results[job_id] = JobResult(status="skipped")
                    pending.remove(job_id)
                    progress = True
                elif all(dep in results for dep in deps) and len(active) < max_concurrency:
                    task = asyncio.create_task(invoke(job_id))
                    active[task] = job_id
                    pending.remove(job_id)
                    progress = True

            if active:
                done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    job_id = active.pop(task)
                    try:
                        results[job_id] = JobResult(status="completed", value=task.result())
                    except asyncio.CancelledError as e:
                        results[job_id] = JobResult(
                            status="failed", error=f"{type(e).__name__}: {e}"
                        )
                    except Exception as e:
                        results[job_id] = JobResult(
                            status="failed", error=f"{type(e).__name__}: {e}"
                        )
            elif pending and not progress:
                # A validated acyclic graph always has a schedulable root here.
                raise RuntimeError("scheduler made no progress")
    except asyncio.CancelledError:
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
        raise

    return {job_id: results[job_id] for job_id in job_ids}


class RunDagTests(unittest.IsolatedAsyncioTestCase):
    async def wait_event(self, event: asyncio.Event) -> None:
        await asyncio.wait_for(event.wait(), timeout=2)

    async def test_dependency_ordering(self) -> None:
        root_started = asyncio.Event()
        release_root = asyncio.Event()
        child_started = asyncio.Event()

        async def worker(job_id: str) -> str:
            if job_id == "root":
                root_started.set()
                await release_root.wait()
            else:
                self.assertTrue(release_root.is_set())
                child_started.set()
            return job_id

        task = asyncio.create_task(
            run_dag({"root": Job(), "child": Job(("root",))}, worker)
        )
        await self.wait_event(root_started)
        self.assertFalse(child_started.is_set())
        release_root.set()
        results = await asyncio.wait_for(task, timeout=2)
        self.assertTrue(child_started.is_set())
        self.assertEqual(results["child"].status, "completed")

    async def test_concurrency_limit_enforcement(self) -> None:
        release = asyncio.Event()
        two_started = asyncio.Event()
        all_started = asyncio.Event()
        active = 0
        peak = 0
        started = 0

        async def worker(job_id: str) -> str:
            nonlocal active, peak, started
            active += 1
            started += 1
            peak = max(peak, active)
            if active == 2:
                two_started.set()
            if started == 3:
                all_started.set()
            await release.wait()
            active -= 1
            return job_id

        task = asyncio.create_task(
            run_dag({name: Job() for name in ("a", "b", "c")}, worker, max_concurrency=2)
        )
        await self.wait_event(two_started)
        self.assertEqual(active, 2)
        self.assertEqual(started, 2)
        release.set()
        await self.wait_event(all_started)
        results = await asyncio.wait_for(task, timeout=2)
        self.assertEqual(peak, 2)
        self.assertEqual(len(results), 3)

    async def test_failure_propagation(self) -> None:
        invoked: list[str] = []

        async def worker(job_id: str) -> str:
            invoked.append(job_id)
            if job_id == "bad":
                raise ValueError("boom")
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
            timeout=2,
        )
        self.assertEqual(results["bad"], JobResult("failed", error="ValueError: boom"))
        self.assertEqual(results["child"], JobResult("skipped"))
        self.assertEqual(results["grandchild"], JobResult("skipped"))
        self.assertEqual(invoked, ["bad"])

    async def test_independent_branches_continue_after_failure(self) -> None:
        independent_ran = asyncio.Event()

        async def worker(job_id: str) -> str:
            if job_id == "bad":
                raise RuntimeError("failure")
            independent_ran.set()
            return job_id

        results = await asyncio.wait_for(
            run_dag(
                {"bad": Job(), "dependent": Job(("bad",)), "independent": Job()},
                worker,
                max_concurrency=1,
            ),
            timeout=2,
        )
        self.assertTrue(independent_ran.is_set())
        self.assertEqual(results["dependent"].status, "skipped")
        self.assertEqual(results["independent"].status, "completed")

    async def test_diamond_graph_execution(self) -> None:
        branches_started = 0
        branches_done: set[str] = set()
        both_started = asyncio.Event()
        release_branches = asyncio.Event()
        join_started = asyncio.Event()

        async def worker(job_id: str) -> str:
            nonlocal branches_started
            if job_id in {"b", "c"}:
                branches_started += 1
                if branches_started == 2:
                    both_started.set()
                await release_branches.wait()
                branches_done.add(job_id)
            elif job_id == "d":
                self.assertEqual(branches_done, {"b", "c"})
                join_started.set()
            return job_id

        task = asyncio.create_task(
            run_dag(
                {"a": Job(), "b": Job(("a",)), "c": Job(("a",)), "d": Job(("b", "c"))},
                worker,
            )
        )
        await self.wait_event(both_started)
        self.assertFalse(join_started.is_set())
        release_branches.set()
        results = await asyncio.wait_for(task, timeout=2)
        self.assertTrue(join_started.is_set())
        self.assertTrue(all(result.status == "completed" for result in results.values()))

    async def test_graph_validation(self) -> None:
        calls = 0

        async def worker(_: str) -> None:
            nonlocal calls
            calls += 1

        invalid_cases = [
            ({"a": Job(("b",)), "b": Job(("a",))}, 1),
            ({"a": Job(("a",))}, 1),
            ({"a": Job(("missing",))}, 1),
            ({"a": Job()}, 0),
            ({"a": Job()}, True),
            ({"a": Job()}, 1.5),
            ({1: Job()}, 1),
            ({"a": object()}, 1),
            ({"a": Job(["a"])}, 1),
            ({"a": Job(([],))}, 1),
            ({"a": Job(("a", "a"))}, 1),
            ({"": Job()}, 1),
        ]
        for graph, concurrency in invalid_cases:
            with self.subTest(graph=graph, concurrency=concurrency):
                with self.assertRaises(ValueError):
                    await run_dag(graph, worker, concurrency)
        with self.assertRaises(ValueError):
            await run_dag({"a": Job()}, None, 1)
        self.assertEqual(calls, 0)

    async def test_stable_return_order(self) -> None:
        jobs = {"last": Job(("first",)), "middle": Job(), "first": Job()}

        async def worker(job_id: str) -> str:
            return job_id

        results = await asyncio.wait_for(run_dag(jobs, worker), timeout=2)
        self.assertEqual(list(results), list(jobs))

    async def test_cancellation_cleanup(self) -> None:
        started = asyncio.Event()
        never = asyncio.Event()
        worker_tasks: list[asyncio.Task] = []
        cleaned: set[str] = set()

        async def worker(job_id: str) -> None:
            task = asyncio.current_task()
            assert task is not None
            worker_tasks.append(task)
            if len(worker_tasks) == 2:
                started.set()
            try:
                await never.wait()
            finally:
                cleaned.add(job_id)

        task = asyncio.create_task(
            run_dag({"a": Job(), "b": Job()}, worker, max_concurrency=2)
        )
        await self.wait_event(started)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
        self.assertEqual(cleaned, {"a", "b"})
        self.assertTrue(all(worker_task.done() for worker_task in worker_tasks))

    async def test_empty_graph(self) -> None:
        async def worker(_: str) -> None:
            self.fail("worker must not run")

        self.assertEqual(await asyncio.wait_for(run_dag({}, worker), timeout=2), {})


if __name__ == "__main__" and "--self-test" in sys.argv:
    unittest.main(module=__name__, argv=[sys.argv[0]], verbosity=2)
