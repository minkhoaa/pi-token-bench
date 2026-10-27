"""Fault-Tolerant Concurrent Job Scheduler."""

import asyncio
import heapq
from typing import Any, Awaitable, Callable

from .models import Job, Result
from .graph import validate_graph


async def execute(
    jobs: dict[str, Job],
    runner: Callable[[str, int], Awaitable[Any]],
    *,
    max_concurrency: int = 3,
) -> dict[str, Result]:
    """Execute a dependency graph with bounded concurrency, retries, and skips."""
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")

    validate_graph(jobs)
    for job in jobs.values():
        if job.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

    if not jobs:
        return {}

    insertion_order = {job_id: index for index, job_id in enumerate(jobs)}
    dependents: dict[str, list[str]] = {job_id: [] for job_id in jobs}
    remaining_dependencies: dict[str, int] = {}
    ready: list[tuple[int, int, str]] = []

    for job_id, job in jobs.items():
        remaining_dependencies[job_id] = len(job.deps)
        for dependency in job.deps:
            dependents[dependency].append(job_id)
        if not job.deps:
            heapq.heappush(ready, (-job.priority, insertion_order[job_id], job_id))

    results: dict[str, Result] = {}
    running: dict[asyncio.Task[Result], str] = {}
    active_resources: set[str] = set()

    async def run_job(job_id: str) -> Result:
        job = jobs[job_id]
        for attempt in range(1, job.max_attempts + 1):
            try:
                value = await runner(job_id, attempt)
            except Exception as exc:
                if attempt == job.max_attempts:
                    return Result(
                        status="FAILED", error=str(exc), attempts=attempt
                    )
            else:
                return Result(status="SUCCESS", value=value, attempts=attempt)
        raise AssertionError("unreachable: max_attempts must be positive")

    def next_ready_job() -> str | None:
        """Choose the highest-priority ready job whose resource is available."""
        deferred: list[tuple[int, int, str]] = []
        selected: str | None = None
        while ready:
            item = heapq.heappop(ready)
            job_id = item[2]
            resource = jobs[job_id].resource
            if resource is None or resource not in active_resources:
                selected = job_id
                break
            deferred.append(item)
        for item in deferred:
            heapq.heappush(ready, item)
        return selected

    def finish_job(job_id: str, result: Result) -> None:
        """Record a terminal result and propagate readiness or skips."""
        results[job_id] = result
        completed = [job_id]
        while completed:
            parent_id = completed.pop()
            parent_succeeded = results[parent_id].status == "SUCCESS"
            for child_id in dependents[parent_id]:
                if child_id in results:
                    continue
                if not parent_succeeded:
                    results[child_id] = Result(status="SKIPPED", attempts=0)
                    completed.append(child_id)
                else:
                    remaining_dependencies[child_id] -= 1
                    if remaining_dependencies[child_id] == 0:
                        child = jobs[child_id]
                        heapq.heappush(
                            ready,
                            (-child.priority, insertion_order[child_id], child_id),
                        )

    try:
        while len(results) < len(jobs):
            while len(running) < max_concurrency:
                job_id = next_ready_job()
                if job_id is None:
                    break
                resource = jobs[job_id].resource
                if resource is not None:
                    active_resources.add(resource)
                task = asyncio.create_task(run_job(job_id))
                running[task] = job_id

            if not running:
                raise RuntimeError("scheduler has unfinished jobs but none are runnable")

            done, _ = await asyncio.wait(
                running, return_when=asyncio.FIRST_COMPLETED
            )
            # Process simultaneous completions deterministically before dispatching
            # newly eligible jobs, so the ready queue can apply priority globally.
            for task in sorted(done, key=lambda item: insertion_order[running[item]]):
                job_id = running.pop(task)
                resource = jobs[job_id].resource
                if resource is not None:
                    active_resources.remove(resource)
                finish_job(job_id, task.result())
    finally:
        pending = list(running)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    return {job_id: results[job_id] for job_id in jobs}
