"""Fault-Tolerant Concurrent Job Scheduler."""

import asyncio
from collections import deque
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
    """Execute a DAG of jobs concurrently with retries and resource exclusivity."""
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")

    validate_graph(jobs)
    if not jobs:
        return {}

    order = {job_id: index for index, job_id in enumerate(jobs)}
    dependents: dict[str, list[str]] = {job_id: [] for job_id in jobs}
    remaining_deps = {job_id: len(job.deps) for job_id, job in jobs.items()}
    for job_id, job in jobs.items():
        for dependency in job.deps:
            dependents[dependency].append(job_id)

    state = {job_id: "pending" for job_id in jobs}
    ready = {job_id for job_id, count in remaining_deps.items() if count == 0}
    for job_id in ready:
        state[job_id] = "ready"

    results: dict[str, Result] = {}
    active: dict[asyncio.Task[Result], tuple[str, str | None]] = {}
    active_resources: set[str] = set()

    async def run_job(job_id: str) -> Result:
        job = jobs[job_id]
        attempt_limit = max(1, job.max_attempts)
        for attempt in range(1, attempt_limit + 1):
            try:
                value = await runner(job_id, attempt)
            except Exception as exc:
                if attempt == attempt_limit:
                    return Result(
                        status="FAILED", error=str(exc), attempts=attempt
                    )
            else:
                return Result(status="SUCCESS", value=value, attempts=attempt)
        raise SchedulerError(f"Job '{job_id}' did not produce a result")

    try:
        while len(results) < len(jobs):
            # Select the highest-priority runnable jobs. A busy resource does not
            # block unrelated ready jobs from using otherwise-free capacity.
            while len(active) < max_concurrency:
                candidates = (
                    job_id
                    for job_id in ready
                    if jobs[job_id].resource is None
                    or jobs[job_id].resource not in active_resources
                )
                job_id = min(
                    candidates,
                    key=lambda item: (-jobs[item].priority, order[item]),
                    default=None,
                )
                if job_id is None:
                    break

                ready.remove(job_id)
                state[job_id] = "running"
                resource = jobs[job_id].resource
                if resource is not None:
                    active_resources.add(resource)
                task = asyncio.create_task(run_job(job_id))
                active[task] = (job_id, resource)

            if not active:
                raise SchedulerError("Scheduler stalled with unfinished jobs")

            done, _ = await asyncio.wait(
                active, return_when=asyncio.FIRST_COMPLETED
            )
            # Process a completion batch before dispatching again, so newly-ready
            # jobs are prioritized together rather than by task-set iteration.
            for task in sorted(done, key=lambda item: order[active[item][0]]):
                job_id, resource = active.pop(task)
                if resource is not None:
                    active_resources.remove(resource)
                outcome = task.result()
                results[job_id] = outcome
                state[job_id] = "finished"

                if outcome.status == "SUCCESS":
                    for dependent in dependents[job_id]:
                        if state[dependent] != "pending":
                            continue
                        remaining_deps[dependent] -= 1
                        if remaining_deps[dependent] == 0:
                            state[dependent] = "ready"
                            ready.add(dependent)
                else:
                    # A failed job makes every transitive dependent impossible.
                    queue = deque(dependents[job_id])
                    while queue:
                        dependent = queue.popleft()
                        if state[dependent] in ("pending", "ready"):
                            ready.discard(dependent)
                            state[dependent] = "finished"
                            results[dependent] = Result(
                                status="SKIPPED", attempts=0
                            )
                        queue.extend(dependents[dependent])
    finally:
        # Do not leave runner tasks alive if execute itself is cancelled or exits
        # unexpectedly.
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)

    # Construct in input order regardless of completion order.
    return {job_id: results[job_id] for job_id in jobs}
