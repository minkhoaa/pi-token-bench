# Fault-Tolerant Concurrent Job Scheduler

A lightweight asynchronous job execution engine designed for executing dependency graphs of jobs with resource exclusivity, retry limits, concurrency limits, priority scheduling, and cascade skipping.

## Project Structure
```
.
├── README.md
├── src/
│   ├── __init__.py
│   ├── exceptions.py   # Domain exceptions
│   ├── graph.py        # Dependency graph validation and topological inspection
│   ├── models.py       # Data models: Job and Result
│   └── scheduler.py    # Core scheduler implementation (EDITABLE FILE)
└── tests/
    ├── __init__.py
    ├── test_basic.py
    ├── test_dependencies.py
    └── test_retry_and_skip.py
```

## Running Tests
Run the test suite using standard Python unittest:
```bash
python3 -m unittest discover -s tests
```
