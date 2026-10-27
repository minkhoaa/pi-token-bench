"""Dependency graph tests for scheduler."""

import asyncio
import unittest
from src.models import Job
from src.exceptions import CycleError, MissingDependencyError
from src.scheduler import execute


class TestDependencyScheduler(unittest.TestCase):
    def test_missing_dependency_raises_error(self):
        jobs = {
            "A": Job(deps=("non_existent",))
        }

        async def runner(job_id: str, attempt: int):
            return True

        with self.assertRaises(MissingDependencyError):
            asyncio.run(execute(jobs, runner))

    def test_cycle_detection_raises_error(self):
        jobs = {
            "A": Job(deps=("B",)),
            "B": Job(deps=("A",)),
        }

        async def runner(job_id: str, attempt: int):
            return True

        with self.assertRaises(CycleError):
            asyncio.run(execute(jobs, runner))

    def test_self_dependency_raises_cycle_error(self):
        jobs = {
            "A": Job(deps=("A",)),
        }

        async def runner(job_id: str, attempt: int):
            return True

        with self.assertRaises(CycleError):
            asyncio.run(execute(jobs, runner))

    def test_diamond_dag_completion(self):
        # A -> B, C -> D
        runs = []

        async def runner(job_id: str, attempt: int):
            runs.append(job_id)
            await asyncio.sleep(0.01)
            return job_id

        jobs = {
            "A": Job(),
            "B": Job(deps=("A",)),
            "C": Job(deps=("A",)),
            "D": Job(deps=("B", "C")),
        }
        results = asyncio.run(execute(jobs, runner, max_concurrency=2))
        self.assertEqual(len(results), 4)
        for k in ["A", "B", "C", "D"]:
            self.assertEqual(results[k].status, "SUCCESS")

        self.assertEqual(runs[0], "A")
        self.assertEqual(runs[-1], "D")
        self.assertSetEqual(set(runs[1:3]), {"B", "C"})


if __name__ == "__main__":
    unittest.main()
