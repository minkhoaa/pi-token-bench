"""Job scheduler data models."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Job:
    """Specification of a unit of work."""
    deps: tuple[str, ...] = ()
    priority: int = 0
    resource: str | None = None
    max_attempts: int = 1


@dataclass(frozen=True)
class Result:
    """Outcome of a job execution."""
    status: str  # "SUCCESS", "FAILED", "SKIPPED", "CANCELLED"
    value: Any = None
    error: str | None = None
    attempts: int = 0
