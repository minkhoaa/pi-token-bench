"""Fault-Tolerant Concurrent Job Scheduler."""

import asyncio
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
    for job_id, job in jobs.items():
        if job.max_attempts < 1:
            raise ValueError(f"Job '{job_id}' must allow at least one attempt")

    job_order = list(jobs)
    job_index = {job_id: index for index, job_id in enumerate(job_order)}
    results: dict[str, Result] = {}
    pending = set(job_order)
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
                        status="FAILED",
                        error=str(exc),
                        attempts=attempt,
                    )
            else:
                return Result(status="SUCCESS", value=value, attempts=attempt)

        raise AssertionError("unreachable")

    try:
        while len(results) < len(jobs):
            # Resolve skips transitively before finding newly eligible work.
            skipped_any = True
            while skipped_any:
                skipped_any = False
                for job_id in job_order:
                    if job_id not in pending:
                        continue
                    if any(
                        dep in results and results[dep].status in {"FAILED", "SKIPPED"}
                        for dep in jobs[job_id].deps
                    ):
                        results[job_id] = Result(status="SKIPPED", attempts=0)
                        pending.remove(job_id)
                        skipped_any = True

            ready = [
                job_id
                for job_id in pending
                if all(
                    dep in results and results[dep].status == "SUCCESS"
                    for dep in jobs[job_id].deps
                )
            ]
            ready.sort(key=lambda job_id: (-jobs[job_id].priority, job_index[job_id]))

            for job_id in ready:
                if len(running) >= max_concurrency:
                    break
                resource = jobs[job_id].resource
                if resource is not None and resource in active_resources:
                    continue
                pending.remove(job_id)
                if resource is not None:
                    active_resources.add(resource)
                running[asyncio.create_task(run_job(job_id))] = job_id

            if not running:
                break

            completed, _ = await asyncio.wait(
                running, return_when=asyncio.FIRST_COMPLETED
            )
            for task in completed:
                job_id = running.pop(task)
                resource = jobs[job_id].resource
                if resource is not None:
                    active_resources.remove(resource)
                results[job_id] = task.result()

        return {job_id: results[job_id] for job_id in job_order}
    except BaseException:
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        raise
