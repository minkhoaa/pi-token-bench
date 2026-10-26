# Task 2: Fault-Tolerant Async DAG Scheduler

Create exactly one Python source file in the current working directory.

## File Naming
The file MUST be named:
`task-2-<current-folder-name>.py`
For example, if the current directory is `test-config-a`, name the file `task-2-test-config-a.py`. Derive the folder name from the current working directory.

## Implementation Specification
Implement the following classes and function:
```python
from dataclasses import dataclass
from typing import Any, Callable, Awaitable

@dataclass(frozen=True)
class Job:
    deps: tuple[str, ...] = ()

@dataclass(frozen=True)
class JobResult:
    status: str
    value: Any = None
    error: str | None = None

async def run_dag(
    jobs: dict[str, Job],
    worker: Callable[[str], Awaitable[Any]],
    max_concurrency: int = 3,
) -> dict[str, JobResult]:
    ...
```

### Requirements:
1. **Dependency Execution:**
   - Respect all dependencies: a job may execute only after all jobs listed in its `deps` have completed successfully (`status == "completed"`).
   - Independent ready jobs must be executed concurrently.
   - Enforce `max_concurrency`: never run more than `max_concurrency` worker tasks simultaneously.
   - Worker must be invoked at most once for each scheduled job: `await worker(job_id)`.
2. **Success & Failure Semantics:**
   - On successful worker completion: `JobResult(status="completed", value=value, error=None)`.
   - On worker exception: convert the exception to `JobResult(status="failed", value=None, error=f"{type(e).__name__}: {e}")`. Do not re-raise worker exceptions.
   - Any transitive descendant of a failed (or skipped) job must not run its worker, and must be marked `JobResult(status="skipped", value=None, error=None)`.
   - Independent branches must continue executing despite failures in other branches.
3. **Ordering & Concurrency Management:**
   - The returned dict must preserve the exact input job order (i.e. keys ordered identically to the input `jobs` dict).
   - Avoid polling or busy-waiting loops (use `asyncio.Event`, `asyncio.Queue`, or task awaiting).
   - Handle simultaneous worker completions cleanly without losing notifications or deadlocking.
4. **Graph Validation:**
   Validate the entire graph before invoking any worker. Raise `ValueError` if:
   - `max_concurrency` is invalid: must be an `int` > 0, excluding booleans (`isinstance(max_concurrency, bool)` or `<= 0`).
   - `worker` is not callable (`not callable(worker)`).
   - Any job ID is invalid (e.g. not a string, or empty string).
   - Any job object is not an instance of `Job`.
   - Any `job.deps` is not a `tuple` (e.g. lists are invalid).
   - Duplicate dependencies exist in a job's `deps`.
   - Any dependency refers to a missing job ID (not defined in `jobs`).
   - Any job has a self-dependency (depends on itself).
   - Any dependency cycle exists (e.g. A -> B -> A).
5. **Special Cases:**
   - An empty graph (`jobs == {}`) must return `{}` immediately without error.
6. **Cancellation Cleanup:**
   - If the calling coroutine/task running `run_dag` is externally cancelled (`asyncio.CancelledError`), it must cancel all currently active child worker tasks, await their cancellation cleanup, ensure no orphaned tasks remain, and propagate `asyncio.CancelledError`.

## Embedded Unit Tests
Support running embedded unit tests:
`python <script-name> --self-test`
Include at least nine deterministic unittest cases in the same file:
1. Dependency ordering (dependent jobs wait for prerequisites)
2. Concurrency limit enforcement (active tasks never exceed `max_concurrency`)
3. Failure propagation (failed job causes downstream descendants to be marked skipped)
4. Independent branches (failure does not stop unrelated parallel jobs)
5. Diamond graph execution (A -> B, C -> D)
6. Graph validation (cycles, self-dependency, missing dep, bad concurrency, bad types)
7. Stable return order (result dict keys match input jobs dict keys)
8. Cancellation cleanup (clean cancellation with no orphan tasks)
9. Empty graph handling

Prefer `asyncio.Event` over arbitrary sleeps. Use bounded test timeouts (`asyncio.timeout` or `asyncio.wait_for`).

## Constraints
- Python 3.11+ standard library only (`asyncio`, `dataclasses`, `unittest`, `typing`, etc.).
- Exactly ONE `.py` source file in the current working directory. No extra files or directories.
- No third-party packages or pip installs.
- No network calls.
- Do not weaken tests or validation to shorten code.

## Verification & Response
1. Run compilation verification (`python -m py_compile <script-name>`).
2. Run self-tests with `--self-test`.
3. Keep the final response concise: report filename, test results, concurrency/cancellation verification, and any failures or retries.
