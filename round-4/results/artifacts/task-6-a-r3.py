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
    """Execute a dependency graph concurrently with retries and resource locks."""
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")

    # Validate the complete graph before creating any runner tasks.
    validate_graph(jobs)
    if any(job.max_attempts < 1 for job in jobs.values()):
        raise ValueError("max_attempts must be at least 1 for every job")
    if not jobs:
        return {}

    order = {job_id: index for index, job_id in enumerate(jobs)}
    pending = set(jobs)
    results: dict[str, Result] = {}
    active: dict[asyncio.Task[Result], str] = {}
    active_resources: set[str] = set()
    concurrency = asyncio.Semaphore(max_concurrency)

    async def run_job(job_id: str) -> Result:
        job = jobs[job_id]
        async with concurrency:
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
                    return Result(
                        status="SUCCESS",
                        value=value,
                        attempts=attempt,
                    )

        raise RuntimeError(f"Job '{job_id}' exhausted no valid attempts")

    try:
        while pending or active:
            # Propagate failures before selecting jobs. Repeating the pass lets
            # skips cascade through arbitrarily long dependency chains at once.
            changed = True
            while changed:
                changed = False
                for job_id in jobs:
                    if job_id not in pending:
                        continue
                    if any(
                        dep in results and results[dep].status != "SUCCESS"
                        for dep in jobs[job_id].deps
                    ):
                        results[job_id] = Result(status="SKIPPED", attempts=0)
                        pending.remove(job_id)
                        changed = True

            # Start the highest-priority dispatchable jobs. Jobs whose resource
            # is occupied are bypassed so an available resource can use capacity.
            while len(active) < max_concurrency:
                ready = sorted(
                    (
                        job_id
                        for job_id in pending
                        if all(
                            dep in results and results[dep].status == "SUCCESS"
                            for dep in jobs[job_id].deps
                        )
                    ),
                    key=lambda job_id: (-jobs[job_id].priority, order[job_id]),
                )
                job_id = next(
                    (
                        candidate
                        for candidate in ready
                        if jobs[candidate].resource is None
                        or jobs[candidate].resource not in active_resources
                    ),
                    None,
                )
                if job_id is None:
                    break

                pending.remove(job_id)
                resource = jobs[job_id].resource
                if resource is not None:
                    active_resources.add(resource)
                task = asyncio.create_task(run_job(job_id), name=f"job:{job_id}")
                active[task] = job_id

            if active:
                completed, _ = await asyncio.wait(
                    active,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in completed:
                    job_id = active.pop(task)
                    resource = jobs[job_id].resource
                    if resource is not None:
                        active_resources.remove(resource)
                    results[job_id] = task.result()
            elif pending:
                # A validated DAG must always have either a ready job or an
                # active prerequisite; this guards against a scheduler deadlock.
                raise RuntimeError("No runnable jobs remain in a validated DAG")
    except BaseException:
        # Do not leave background runners alive if execute itself is cancelled
        # or an unexpected internal error escapes.
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        raise

    # Build the final mapping from input order, independent of completion order.
    return {job_id: results[job_id] for job_id in jobs}
