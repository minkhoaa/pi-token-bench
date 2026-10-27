"""Scheduler exception hierarchy."""


class SchedulerError(Exception):
    """Base exception for all scheduler errors."""
    pass


class GraphValidationError(SchedulerError):
    """Raised when job graph structure is invalid."""
    pass


class CycleError(GraphValidationError):
    """Raised when a dependency cycle is detected in the job graph."""
    pass


class MissingDependencyError(GraphValidationError):
    """Raised when a job references an unknown dependency."""
    pass
