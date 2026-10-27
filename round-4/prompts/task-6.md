# Task 6: Fault-Tolerant Concurrent Job Scheduler

You are tasked with diagnosing and implementing a fault-tolerant asynchronous concurrent job scheduler in the current working directory.

## Repository Overview
Inspect the existing repository structure:
- `src/scheduler.py`: Core scheduler implementation containing `execute`. **This is the ONLY file you are permitted to modify.**
- `src/models.py`: Data models defining `Job` and `Result`.
- `src/exceptions.py`: Custom domain exceptions (`SchedulerError`, `GraphValidationError`, `CycleError`, `MissingDependencyError`).
- `src/graph.py`: Graph validation and topological inspection utilities (`validate_graph`, `get_descendants`).
- `tests/`: Automated test suite containing `test_basic.py`, `test_dependencies.py`, and `test_retry_and_skip.py`.

## Data Models and Public API
The scheduler operates on the following models in `src/models.py`:
```python
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

@dataclass(frozen=True)
class Job:
    deps: tuple[str, ...] = ()
    priority: int = 0
    resource: str | None = None
    max_attempts: int = 1

@dataclass(frozen=True)
class Result:
    status: str  # "SUCCESS", "FAILED", "SKIPPED", "CANCELLED"
    value: Any = None
    error: str | None = None
    attempts: int = 0
```

The main entry point in `src/scheduler.py` is:
```python
async def execute(
    jobs: dict[str, Job],
    runner: Callable[[str, int], Awaitable[Any]],
    *,
    max_concurrency: int = 3,
) -> dict[str, Result]:
    ...
```

The `runner` callable takes two arguments:
1. `job_id: str` - the identifier of the job being executed.
2. `attempt: int` - the 1-based attempt count (1 for first run, 2 for first retry, etc.).

It returns an awaitable resolving to the job's result value or raises an Exception.

## Objectives & Requirements
The starter implementation in `src/scheduler.py` is incomplete and defective. Repair and complete `src/scheduler.py` so that it satisfies all of the following requirements:

1. **DAG Graph Validation:**
   - Before executing any job or invoking `runner`, validate the dependency graph.
   - If any job specifies a dependency not present in `jobs`, raise `MissingDependencyError` (subclass of `GraphValidationError`).
   - If any dependency cycle exists (including self-dependencies like `deps=("A",)` on job `"A"`), raise `CycleError` (subclass of `GraphValidationError`).
   - You may use `validate_graph(jobs)` from `src.graph` or provide equivalent validation.

2. **Concurrency Limiting:**
   - At no point should more than `max_concurrency` jobs be executing concurrently.
   - If `max_concurrency < 1`, raise `ValueError`.

3. **Resource Mutual Exclusivity:**
   - Jobs may declare a `resource` name (e.g., `"gpu"`, `"cpu"`, `"db"`).
   - If two or more jobs specify the same non-`None` resource, they must NEVER execute concurrently, even if overall concurrency capacity remains available.
   - Jobs with different resources or `resource=None` may execute concurrently up to `max_concurrency`.

4. **Priority Scheduling & Tie Breaking:**
   - When multiple jobs become eligible/ready to run, schedule jobs with higher integer `priority` first.
   - When two eligible jobs have identical `priority`, tie-break by their insertion order in the input `jobs` dictionary (earlier in `jobs` runs first).

5. **Retry Mechanism:**
   - If a runner invocation raises an exception and `attempt < job.max_attempts`, retry the job up to `max_attempts`.
   - If a job succeeds on attempt `k <= max_attempts`, record its `Result` with `status="SUCCESS"`, `value=result`, and `attempts=k`.
   - If a job fails on attempt `max_attempts`, record its `Result` with `status="FAILED"`, `error=str(exc)`, and `attempts=max_attempts`.

6. **Cascading Skip Propagation:**
   - If a job permanently fails (all attempts exhausted), all downstream jobs that directly or indirectly depend on it must be marked with `status="SKIPPED"`, `attempts=0`, and `error=None`.
   - The runner must NEVER be called for skipped jobs.
   - Independent branches or jobs not depending on failed jobs must continue executing normally to completion.

7. **Event-Driven Non-Blocking Coordination:**
   - The scheduler must be purely event-driven using standard `asyncio` primitives (`asyncio.Semaphore`, `asyncio.Event`, `asyncio.Lock`, `asyncio.create_task`, `asyncio.wait`).
   - Do NOT use polling loops with `asyncio.sleep` to wait for dependencies or resource locks.

8. **Result Dictionary Key Ordering:**
   - The returned `dict[str, Result]` must preserve the exact key ordering of the input `jobs` dictionary.

## Strict Constraints
- **Modify ONLY `src/scheduler.py`**. Do not modify tests, fixtures, `src/models.py`, `src/exceptions.py`, or `src/graph.py`.
- Python 3.11+ standard library only (`asyncio`, `typing`, `dataclasses`, `collections`, etc.).
- Do not install external packages or make network calls.
- Do not disable, delete, or weaken existing tests in `tests/`.

## Verification
- Run existing test suites: `python3 -m unittest discover -s tests`.
- Add any focused temporary checks if needed, but remove temporary test files before concluding.
- Ensure all public tests pass and proper concurrency, ordering, retry, and skipping behaviors are respected.
