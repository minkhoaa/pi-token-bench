"""Fault-tolerant concurrent job scheduler."""

import asyncio
import heapq
from typing import Any, Awaitable, Callable

from .exceptions import SchedulerError
from .graph import validate_graph
from .models import Job, Result


async def execute(
    jobs: dict[str, Job],
    runner: Callable[[str, int], Awaitable[Any]],
    *,
    max_concurrency: int = 3,
) -> dict[str, Result]:
    """Execute dependency-ready jobs concurrently, with retries and resource locks."""
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")

    validate_graph(jobs)
    if not jobs:
        return {}

    order = {job_id: index for index, job_id in enumerate(jobs)}
    dependents: dict[str, list[str]] = {job_id: [] for job_id in jobs}
    remaining: dict[str, int] = {}
    ready: list[tuple[int, int, str]] = []

    for job_id, job in jobs.items():
        remaining[job_id] = len(job.deps)
        for dependency in job.deps:
            dependents[dependency].append(job_id)
        if not job.deps:
            heapq.heappush(ready, (-job.priority, order[job_id], job_id))

    async def run_job(job_id: str, job: Job) -> Result:
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

        # max_attempts is expected to be positive; keep malformed jobs terminal.
        return Result(status="FAILED", error=None, attempts=0)

    results: dict[str, Result] = {}
    running: dict[asyncio.Task[Result], tuple[str, str | None]] = {}
    resources_in_use: set[str] = set()

    try:
        while len(results) < len(jobs):
            blocked: list[tuple[int, int, str]] = []
            while ready and len(running) < max_concurrency:
                entry = heapq.heappop(ready)
                _, _, job_id = entry
                if job_id in results:
                    continue

                resource = jobs[job_id].resource
                if resource is not None and resource in resources_in_use:
                    blocked.append(entry)
                    continue

                task = asyncio.create_task(run_job(job_id, jobs[job_id]))
                running[task] = (job_id, resource)
                if resource is not None:
                    resources_in_use.add(resource)

            for entry in blocked:
                heapq.heappush(ready, entry)

            if not running:
                raise SchedulerError("No runnable jobs remain in a valid dependency graph")

            completed, _ = await asyncio.wait(
                running, return_when=asyncio.FIRST_COMPLETED
            )
            for task in completed:
                job_id, resource = running.pop(task)
                if resource is not None:
                    resources_in_use.remove(resource)

                result = task.result()
                results[job_id] = result

                if result.status == "SUCCESS":
                    for dependent in dependents[job_id]:
                        if dependent in results:
                            continue
                        remaining[dependent] -= 1
                        if remaining[dependent] == 0:
                            heapq.heappush(
                                ready,
                                (-jobs[dependent].priority, order[dependent], dependent),
                            )
                else:
                    pending = list(dependents[job_id])
                    while pending:
                        descendant = pending.pop()
                        if descendant in results:
                            continue
                        results[descendant] = Result(
                            status="SKIPPED", attempts=0
                        )
                        pending.extend(dependents[descendant])
    finally:
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)

    return {job_id: results[job_id] for job_id in jobs}
