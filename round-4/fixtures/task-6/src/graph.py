"""Graph validation and dependency inspection utilities."""

from typing import Mapping
from .models import Job
from .exceptions import CycleError, MissingDependencyError


def validate_graph(jobs: Mapping[str, Job]) -> None:
    """Validate that the job graph has no missing dependencies and no cycles.

    Raises:
        MissingDependencyError: if any job depends on a non-existent job id.
        CycleError: if there is a cycle (including self-dependency).
    """
    job_keys = set(jobs.keys())

    # 1. Check for missing dependencies
    for job_id, job in jobs.items():
        for dep in job.deps:
            if dep not in job_keys:
                raise MissingDependencyError(
                    f"Job '{job_id}' depends on missing job '{dep}'"
                )

    # 2. Cycle detection via DFS with 3 colors (0=unvisited, 1=visiting, 2=visited)
    state: dict[str, int] = {k: 0 for k in jobs}

    def dfs(node: str) -> None:
        state[node] = 1  # visiting
        for dep in jobs[node].deps:
            if state[dep] == 1:
                raise CycleError(f"Dependency cycle detected involving '{dep}' and '{node}'")
            if state[dep] == 0:
                dfs(dep)
        state[node] = 2  # visited

    for job_id in jobs:
        if state[job_id] == 0:
            dfs(job_id)


def get_descendants(jobs: Mapping[str, Job], target: str) -> set[str]:
    """Return all downstream descendants that transitively depend on target."""
    # Build forward adjacency list: u -> list of jobs that depend on u
    dependents: dict[str, list[str]] = {k: [] for k in jobs}
    for job_id, job in jobs.items():
        for dep in job.deps:
            if dep in dependents:
                dependents[dep].append(job_id)

    visited: set[str] = set()
    queue = list(dependents.get(target, []))
    while queue:
        curr = queue.pop()
        if curr not in visited:
            visited.add(curr)
            queue.extend(dependents.get(curr, []))

    return visited
