"""Fault-Tolerant Concurrent Job Scheduler."""

import asyncio
import heapq
from typing import Any, Awaitable, Callable

from .graph import validate_graph
from .models import Job, Result


async def execute(
    jobs: dict[str, Job],
    runner: Callable[[str, int], Awaitable[Any]],
    *,
    max_concurrency: int = 3,
) -> dict[str, Result]:
    """Execute a DAG concurrently, retrying failures and skipping dependents."""
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")

    validate_graph(jobs)
    if not jobs:
        return {}

    order = {job_id: index for index, job_id in enumerate(jobs)}
    dependents: dict[str, list[str]] = {job_id: [] for job_id in jobs}
    remaining = {job_id: len(job.deps) for job_id, job in jobs.items()}
    for job_id, job in jobs.items():
        for dependency in job.deps:
            dependents[dependency].append(job_id)

    # Heap key: higher priority first, then original input order.
    ready: list[tuple[int, int, str]] = []

    def enqueue(job_id: str) -> None:
        heapq.heappush(ready, (-jobs[job_id].priority, order[job_id], job_id))

    for job_id, count in remaining.items():
        if count == 0:
            enqueue(job_id)

    results: dict[str, Result] = {}
    running: dict[asyncio.Task[Result], str] = {}
    active_resources: set[str] = set()

    async def run_job(job_id: str) -> Result:
        job = jobs[job_id]
        for attempt in range(1, job.max_attempts + 1):
            try:
                value = await runner(job_id, attempt)
                return Result(status="SUCCESS", value=value, attempts=attempt)
            except Exception as exc:
                if attempt == job.max_attempts:
                    return Result(status="FAILED", error=str(exc), attempts=attempt)
        # max_attempts is expected to be positive; keep malformed specs from
        # leaving the scheduler waiting forever.
        return Result(status="FAILED", error="max_attempts must be at least 1", attempts=0)

    def finish(job_id: str, result: Result) -> None:
        """Record a result and propagate newly ready or skipped descendants."""
        pending = [(job_id, result)]
        while pending:
            completed_id, completed_result = pending.pop()
            results[completed_id] = completed_result
            for child in dependents[completed_id]:
                remaining[child] -= 1
                if remaining[child] == 0:
                    if any(results[dep].status != "SUCCESS" for dep in jobs[child].deps):
                        pending.append((child, Result(status="SKIPPED", attempts=0)))
                    else:
                        enqueue(child)

    try:
        while len(results) < len(jobs):
            # Fill every available slot with the highest-priority ready job
            # whose exclusive resource is currently free. A blocked resource
            # does not prevent unrelated work from using spare capacity.
            while len(running) < max_concurrency and ready:
                blocked: list[tuple[int, int, str]] = []
                selected: tuple[int, int, str] | None = None
                while ready:
                    candidate = heapq.heappop(ready)
                    resource = jobs[candidate[2]].resource
                    if resource is None or resource not in active_resources:
                        selected = candidate
                        break
                    blocked.append(candidate)
                for candidate in blocked:
                    heapq.heappush(ready, candidate)
                if selected is None:
                    break

                job_id = selected[2]
                resource = jobs[job_id].resource
                if resource is not None:
                    active_resources.add(resource)
                running[asyncio.create_task(run_job(job_id))] = job_id

            if not running:
                # With a validated DAG, this can only occur if every job has
                # already been completed (including propagated skips).
                break

            completed, _ = await asyncio.wait(
                running, return_when=asyncio.FIRST_COMPLETED
            )
            for task in completed:
                job_id = running.pop(task)
                resource = jobs[job_id].resource
                if resource is not None:
                    active_resources.remove(resource)
                finish(job_id, task.result())
    finally:
        # Do not leave runner tasks orphaned if execute itself is cancelled
        # or a runner raises a non-Exception BaseException.
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)

    return {job_id: results[job_id] for job_id in jobs}
