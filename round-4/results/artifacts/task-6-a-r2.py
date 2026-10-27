"""Fault-Tolerant Concurrent Job Scheduler."""

import asyncio
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
    """Execute a validated job DAG with retries and resource exclusivity."""
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")
    # Validate before scheduling anything, so an invalid graph never invokes runner.
    validate_graph(jobs)
    if not jobs:
        return {}

    for job_id, job in jobs.items():
        if job.max_attempts < 1:
            raise ValueError(f"Job '{job_id}' must allow at least one attempt")

    order = {job_id: index for index, job_id in enumerate(jobs)}
    remaining = set(jobs)
    results: dict[str, Result] = {}
    running: dict[asyncio.Task[Result], str] = {}
    active_resources: set[str] = set()
    slots = asyncio.Semaphore(max_concurrency)

    async def run_job(job_id: str, job: Job) -> Result:
        # The coordinator also accounts for slots before creating tasks. Keeping a
        # semaphore here makes the concurrency limit explicit at the execution
        # boundary as well.
        async with slots:
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
        # Unreachable because max_attempts is validated as positive.
        raise SchedulerError(f"Job '{job_id}' completed without a result")

    def skip_unrunnable_jobs() -> None:
        """Mark all pending descendants of failed/skipped jobs as skipped."""
        changed = True
        while changed:
            changed = False
            for job_id in jobs:
                if job_id not in remaining:
                    continue
                if any(
                    dep in results and results[dep].status != "SUCCESS"
                    for dep in jobs[job_id].deps
                ):
                    results[job_id] = Result(status="SKIPPED", attempts=0)
                    remaining.remove(job_id)
                    changed = True

    try:
        while remaining or running:
            skip_unrunnable_jobs()

            # Choose the highest-priority currently runnable jobs, with input
            # insertion order as the stable tie breaker. Jobs sharing a resource
            # are considered mutually exclusive in this dispatch as well.
            ready = sorted(
                (
                    job_id
                    for job_id in remaining
                    if all(
                        dep in results and results[dep].status == "SUCCESS"
                        for dep in jobs[job_id].deps
                    )
                ),
                key=lambda job_id: (-jobs[job_id].priority, order[job_id]),
            )
            dispatch_resources = set(active_resources)
            while len(running) < max_concurrency:
                selected = next(
                    (
                        job_id
                        for job_id in ready
                        if jobs[job_id].resource is None
                        or jobs[job_id].resource not in dispatch_resources
                    ),
                    None,
                )
                if selected is None:
                    break
                ready.remove(selected)
                remaining.remove(selected)
                resource = jobs[selected].resource
                if resource is not None:
                    dispatch_resources.add(resource)
                    active_resources.add(resource)
                task = asyncio.create_task(run_job(selected, jobs[selected]))
                running[task] = selected

            if not running:
                if remaining:
                    # A validated DAG should always have a ready job unless all
                    # remaining work has just become skipped above.
                    raise SchedulerError("Scheduler stalled with pending jobs")
                break

            done, _ = await asyncio.wait(
                running, return_when=asyncio.FIRST_COMPLETED
            )
            # Processing simultaneous completions in input order keeps readiness
            # updates deterministic; the next dispatch then applies priority.
            for task in sorted(done, key=lambda item: order[running[item]]):
                job_id = running.pop(task)
                resource = jobs[job_id].resource
                if resource is not None:
                    active_resources.discard(resource)
                results[job_id] = task.result()
    except BaseException:
        # Do not leave runner tasks running if execute itself is cancelled or
        # encounters an unexpected scheduler error.
        tasks = list(running)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        raise

    # Construct explicitly in input order, independent of completion order.
    return {job_id: results[job_id] for job_id in jobs}
