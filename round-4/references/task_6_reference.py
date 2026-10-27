"""Fault-Tolerant Concurrent Job Scheduler Reference Implementation.

This module implements:
- Graph validation (cycle detection, missing dependency check).
- Concurrency limiting via asyncio.Semaphore.
- Resource exclusivity via mutual exclusion locks per resource.
- Priority scheduling (higher priority integer runs first; tie-broken by input dict order).
- Retries up to max_attempts.
- Cascading skip propagation to descendants when a dependency fails or is skipped.
- Event-driven state updates (no polling / sleep loops).
- Result dictionary keys preserving original jobs dict insertion order.
"""

from dataclasses import dataclass
from typing import Any, Callable, Awaitable
import asyncio

from src.models import Job, Result
from src.exceptions import SchedulerError, GraphValidationError, CycleError, MissingDependencyError
from src.graph import validate_graph, get_descendants


async def execute(
    jobs: dict[str, Job],
    runner: Callable[[str, int], Awaitable[Any]],
    *,
    max_concurrency: int = 3,
) -> dict[str, Result]:
    """Execute a DAG of jobs concurrently with retries and resource exclusivity."""
    if not jobs:
        return {}

    # 1. Validate graph structure before invoking runner
    validate_graph(jobs)

    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")

    # 2. Setup state structures
    # Preserve input ordering for tie-breaking and final dict
    original_keys = list(jobs.keys())
    key_order_index = {k: i for i, k in enumerate(original_keys)}

    # Graph relationships
    # dependents[u] = list of jobs v that depend on u
    dependents: dict[str, list[str]] = {k: [] for k in jobs}
    # unsatisfied_deps[v] = set of dependency job keys that must succeed before v can run
    unsatisfied_deps: dict[str, set[str]] = {k: set(job.deps) for k, job in jobs.items()}

    for k, job in jobs.items():
        for dep in job.deps:
            dependents[dep].append(k)

    # Job execution status tracking
    # States: PENDING, RUNNING, COMPLETED
    results: dict[str, Result] = {}
    completed_events: dict[str, asyncio.Event] = {k: asyncio.Event() for k in jobs}

    # Concurrency and resource synchronization
    global_sem = asyncio.Semaphore(max_concurrency)
    resource_locks: dict[str, asyncio.Lock] = {}
    for job in jobs.values():
        if job.resource is not None and job.resource not in resource_locks:
            resource_locks[job.resource] = asyncio.Lock()

    # Event triggered whenever any job completes or state changes, to notify dispatcher
    state_changed_event = asyncio.Event()

    # Set of jobs currently eligible to run or running
    # Ready queue tracked with priorities
    active_tasks: set[asyncio.Task] = set()
    scheduled_or_running: set[str] = set()

    def get_ready_jobs() -> list[str]:
        """Find pending jobs whose dependencies are all satisfied and not yet scheduled."""
        ready = []
        for k in original_keys:
            if k not in results and k not in scheduled_or_running:
                if len(unsatisfied_deps[k]) == 0:
                    ready.append(k)
        # Sort ready jobs: higher priority first (descending), then original key index (ascending)
        ready.sort(key=lambda k: (-jobs[k].priority, key_order_index[k]))
        return ready

    async def run_single_job(job_id: str):
        job = jobs[job_id]
        res_lock = resource_locks.get(job.resource) if job.resource is not None else None

        # Concurrency limit acquisition
        async with global_sem:
            if res_lock:
                async with res_lock:
                    await _execute_with_retries(job_id, job)
            else:
                await _execute_with_retries(job_id, job)

    async def _execute_with_retries(job_id: str, job: Job):
        max_attempts = max(1, job.max_attempts)
        attempts = 0
        last_error = None

        while attempts < max_attempts:
            attempts += 1
            try:
                val = await runner(job_id, attempts)
                results[job_id] = Result(status="SUCCESS", value=val, attempts=attempts)
                _on_job_success(job_id)
                return
            except Exception as e:
                last_error = str(e)

        # Retries exhausted -> FAILED
        results[job_id] = Result(status="FAILED", error=last_error, attempts=attempts)
        _on_job_failure_or_skip(job_id)

    def _on_job_success(job_id: str):
        # Remove job_id from unsatisfied_deps of its dependents
        for dep in dependents[job_id]:
            unsatisfied_deps[dep].discard(job_id)
        completed_events[job_id].set()
        state_changed_event.set()

    def _on_job_failure_or_skip(job_id: str):
        # All downstream descendants must be marked SKIPPED
        descendants = get_descendants(jobs, job_id)
        for desc in descendants:
            if desc not in results:
                results[desc] = Result(status="SKIPPED", attempts=0)
                completed_events[desc].set()
                # Also remove unsatisfied deps so they don't block anything
                unsatisfied_deps[desc].clear()
        completed_events[job_id].set()
        state_changed_event.set()

    # Dispatcher loop
    try:
        while len(results) < len(jobs):
            # Check for newly ready jobs
            ready = get_ready_jobs()

            # Schedule available ready jobs that don't violate resource contention
            scheduled_any = False
            for job_id in ready:
                job = jobs[job_id]
                res_lock = resource_locks.get(job.resource) if job.resource is not None else None

                # Check if resource is currently locked before launching to respect priority queueing
                if res_lock is not None and res_lock.locked():
                    continue

                scheduled_or_running.add(job_id)
                task = asyncio.create_task(run_single_job(job_id))
                active_tasks.add(task)
                scheduled_any = True

            # If no tasks are currently running and nothing could be scheduled,
            # but results < len(jobs), check if any are remaining
            if not active_tasks and not scheduled_any:
                # If there are pending jobs but none can run and nothing active, break to avoid hang
                break

            # Wait for any task to finish or state to change
            state_changed_event.clear()

            done, _ = await asyncio.wait(
                active_tasks,
                return_when=asyncio.FIRST_COMPLETED
            )
            for t in done:
                active_tasks.remove(t)
                # Check for unexpected task exceptions
                if not t.cancelled() and t.exception() is not None:
                    raise t.exception()
    finally:
        # Cleanup any remaining active tasks if cancelled or error
        if active_tasks:
            for t in active_tasks:
                t.cancel()
            await asyncio.gather(*active_tasks, return_exceptions=True)

    # Return results in the exact insertion order of jobs dict
    ordered_results: dict[str, Result] = {k: results[k] for k in original_keys if k in results}
    return ordered_results
