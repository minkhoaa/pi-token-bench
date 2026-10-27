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
    """Execute a dependency graph, respecting priorities and resource limits."""
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")

    # Validate the complete graph before creating any runner tasks.
    validate_graph(jobs)
    if not jobs:
        return {}

    order = {job_id: index for index, job_id in enumerate(jobs)}
    dependents: dict[str, list[str]] = {job_id: [] for job_id in jobs}
    remaining_deps: dict[str, int] = {}
    for job_id, job in jobs.items():
        remaining_deps[job_id] = len(job.deps)
        for dep in job.deps:
            dependents[dep].append(job_id)

    # State is owned by this coordinator; runner tasks only return outcomes.
    state = {job_id: "PENDING" for job_id in jobs}
    results: dict[str, Result] = {}
    ready: list[tuple[int, int, str]] = []
    for job_id, job in jobs.items():
        if remaining_deps[job_id] == 0:
            state[job_id] = "READY"
            heapq.heappush(ready, (-job.priority, order[job_id], job_id))

    active_resources: set[str] = set()
    running: dict[asyncio.Task[Result], str] = {}

    async def run_job(job_id: str) -> Result:
        job = jobs[job_id]
        # max_attempts is expected to be positive; still make a malformed zero
        # behave as a single attempt rather than silently dropping the job.
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
        raise AssertionError("unreachable")

    def take_runnable() -> str | None:
        """Take the highest-priority ready job whose resource is currently free."""
        deferred: list[tuple[int, int, str]] = []
        chosen: str | None = None
        while ready:
            entry = heapq.heappop(ready)
            job_id = entry[2]
            if state[job_id] != "READY":
                continue
            resource = jobs[job_id].resource
            if resource is None or resource not in active_resources:
                chosen = job_id
                break
            deferred.append(entry)
        for entry in deferred:
            heapq.heappush(ready, entry)
        return chosen

    try:
        while len(results) < len(jobs):
            # Fill all available slots in priority order. Resource-blocked jobs
            # remain queued while other runnable jobs may use the free slots.
            while len(running) < max_concurrency:
                job_id = take_runnable()
                if job_id is None:
                    break
                state[job_id] = "RUNNING"
                resource = jobs[job_id].resource
                if resource is not None:
                    active_resources.add(resource)
                task = asyncio.create_task(run_job(job_id))
                running[task] = job_id

            if not running:
                # A validated DAG can only become stuck here if scheduler state
                # is inconsistent; avoid spinning if that ever happens.
                if len(results) != len(jobs):
                    raise RuntimeError("scheduler has unfinished jobs but none can run")
                break

            done, _ = await asyncio.wait(
                running, return_when=asyncio.FIRST_COMPLETED
            )

            completed: list[tuple[str, Result]] = []
            for task in sorted(done, key=lambda item: order[running[item]]):
                job_id = running.pop(task)
                resource = jobs[job_id].resource
                if resource is not None:
                    active_resources.remove(resource)
                completed.append((job_id, task.result()))

            # Commit the whole completion batch before making more scheduling
            # decisions, so simultaneous completions are handled consistently.
            for job_id, result in completed:
                state[job_id] = "DONE"
                results[job_id] = result

            for job_id, result in completed:
                if result.status != "SUCCESS":
                    continue
                for child in dependents[job_id]:
                    if state[child] != "PENDING":
                        continue
                    remaining_deps[child] -= 1
                    if remaining_deps[child] == 0:
                        state[child] = "READY"
                        heapq.heappush(
                            ready, (-jobs[child].priority, order[child], child)
                        )

            # A permanent failure makes every downstream job un-runnable. Mark
            # skips transitively without waiting for unrelated dependencies.
            to_propagate = [
                job_id for job_id, result in completed
                if result.status == "FAILED"
            ]
            while to_propagate:
                failed_parent = to_propagate.pop()
                for child in dependents[failed_parent]:
                    if state[child] in ("DONE", "RUNNING"):
                        continue
                    state[child] = "DONE"
                    results[child] = Result(status="SKIPPED", attempts=0)
                    to_propagate.append(child)
    finally:
        # Do not orphan runner tasks if execute itself is cancelled or an
        # unexpected task error escapes the coordinator.
        outstanding = list(running)
        for task in outstanding:
            task.cancel()
        if outstanding:
            await asyncio.gather(*outstanding, return_exceptions=True)

    # Build in input order, independent of task completion order.
    return {job_id: results[job_id] for job_id in jobs}
