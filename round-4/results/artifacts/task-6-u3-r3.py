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
    """Execute a DAG of jobs concurrently with retries and resource exclusivity."""
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")

    validate_graph(jobs)
    if not jobs:
        return {}

    job_ids = tuple(jobs)
    order = {job_id: index for index, job_id in enumerate(job_ids)}
    dependents: dict[str, list[str]] = {job_id: [] for job_id in job_ids}
    remaining = {job_id: len(job.deps) for job_id, job in jobs.items()}
    for job_id, job in jobs.items():
        for dep in job.deps:
            dependents[dep].append(job_id)

    # Heap key: highest priority first, then input insertion order.
    ready = [
        (-jobs[job_id].priority, order[job_id], job_id)
        for job_id in job_ids
        if remaining[job_id] == 0
    ]
    heapq.heapify(ready)

    results: dict[str, Result] = {}
    active: dict[asyncio.Task[Result], str] = {}
    active_resources: set[str] = set()
    slots = asyncio.Semaphore(max_concurrency)

    async def run_job(job_id: str, job: Job) -> Result:
        async with slots:
            attempt = 1
            while True:
                try:
                    value = await runner(job_id, attempt)
                    return Result(status="SUCCESS", value=value, attempts=attempt)
                except Exception as exc:
                    if attempt >= job.max_attempts:
                        return Result(
                            status="FAILED", error=str(exc), attempts=attempt
                        )
                    attempt += 1

    def skip_descendants(job_id: str) -> None:
        pending = list(dependents[job_id])
        while pending:
            child = pending.pop()
            if child in results:
                continue
            results[child] = Result(status="SKIPPED", attempts=0)
            pending.extend(dependents[child])

    try:
        while len(results) < len(jobs):
            blocked = []
            while ready and len(active) < max_concurrency:
                entry = heapq.heappop(ready)
                job_id = entry[2]
                resource = jobs[job_id].resource
                if resource is not None and resource in active_resources:
                    blocked.append(entry)
                    continue

                task = asyncio.create_task(run_job(job_id, jobs[job_id]))
                active[task] = job_id
                if resource is not None:
                    active_resources.add(resource)

            for entry in blocked:
                heapq.heappush(ready, entry)

            if not active:
                # A validated DAG always has a runnable job unless all jobs resolved.
                raise RuntimeError("scheduler has unresolved jobs but no runnable work")

            done, _ = await asyncio.wait(
                active, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                job_id = active.pop(task)
                resource = jobs[job_id].resource
                if resource is not None:
                    active_resources.remove(resource)

                result = task.result()
                results[job_id] = result
                if result.status == "SUCCESS":
                    for child in dependents[job_id]:
                        if child in results:
                            continue
                        remaining[child] -= 1
                        if remaining[child] == 0:
                            heapq.heappush(
                                ready,
                                (-jobs[child].priority, order[child], child),
                            )
                else:
                    skip_descendants(job_id)
    finally:
        pending_tasks = list(active)
        for task in pending_tasks:
            if not task.done():
                task.cancel()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)

    return {job_id: results[job_id] for job_id in job_ids}
