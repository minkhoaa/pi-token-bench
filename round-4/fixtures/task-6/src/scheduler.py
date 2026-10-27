"""Fault-Tolerant Concurrent Job Scheduler."""

from dataclasses import dataclass
from typing import Any, Callable, Awaitable
import asyncio

from .models import Job, Result
from .exceptions import SchedulerError, GraphValidationError, CycleError, MissingDependencyError
from .graph import validate_graph


async def execute(
    jobs: dict[str, Job],
    runner: Callable[[str, int], Awaitable[Any]],
    *,
    max_concurrency: int = 3,
) -> dict[str, Result]:
    """Execute a DAG of jobs concurrently with retries and resource exclusivity.

    NOTE: This is a naive/defective starter implementation.
    """
    if not jobs:
        return {}

    # Defect: Missing graph validation (does not call validate_graph)
    # Defect: Does not enforce resource exclusivity
    # Defect: Ignores max_attempts and does not retry failed jobs
    # Defect: Does not skip descendants when a dependency fails
    # Defect: Runs everything sequentially or naively without proper concurrency limits / priorities

    results: dict[str, Result] = {}

    for job_id, job in jobs.items():
        try:
            val = await runner(job_id, 1)
            results[job_id] = Result(status="SUCCESS", value=val, attempts=1)
        except Exception as e:
            results[job_id] = Result(status="FAILED", error=str(e), attempts=1)

    return results
