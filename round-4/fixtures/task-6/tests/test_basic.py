"""Basic execution tests for scheduler."""

import asyncio
import unittest
from src.models import Job, Result
from src.scheduler import execute


class TestBasicScheduler(unittest.TestCase):
    def test_empty_jobs(self):
        async def dummy_runner(job_id: str, attempt: int):
            return 42

        results = asyncio.run(execute({}, dummy_runner))
        self.assertEqual(results, {})

    def test_single_job_success(self):
        async def runner(job_id: str, attempt: int):
            return f"done-{job_id}"

        jobs = {"A": Job()}
        results = asyncio.run(execute(jobs, runner))
        self.assertIn("A", results)
        self.assertEqual(results["A"].status, "SUCCESS")
        self.assertEqual(results["A"].value, "done-A")
        self.assertEqual(results["A"].attempts, 1)

    def test_linear_dag_execution_order(self):
        executed_order = []

        async def runner(job_id: str, attempt: int):
            executed_order.append(job_id)
            await asyncio.sleep(0.01)
            return job_id

        jobs = {
            "C": Job(deps=("B",)),
            "B": Job(deps=("A",)),
            "A": Job(),
        }
        results = asyncio.run(execute(jobs, runner, max_concurrency=2))
        self.assertEqual(executed_order, ["A", "B", "C"])
        for k in ["A", "B", "C"]:
            self.assertEqual(results[k].status, "SUCCESS")
            self.assertEqual(results[k].value, k)


if __name__ == "__main__":
    unittest.main()
