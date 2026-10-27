"""Retry and cascading skip tests for scheduler."""

import asyncio
import unittest
from src.models import Job, Result
from src.scheduler import execute


class TestRetryAndSkip(unittest.TestCase):
    def test_retry_eventual_success(self):
        call_counts = {}

        async def runner(job_id: str, attempt: int):
            call_counts[job_id] = attempt
            if attempt < 3:
                raise ValueError(f"Temporary failure on attempt {attempt}")
            return "recovered"

        jobs = {
            "A": Job(max_attempts=3)
        }
        results = asyncio.run(execute(jobs, runner))
        self.assertEqual(results["A"].status, "SUCCESS")
        self.assertEqual(results["A"].value, "recovered")
        self.assertEqual(results["A"].attempts, 3)

    def test_retry_exhaustion_failure(self):
        async def runner(job_id: str, attempt: int):
            raise RuntimeError("Permanent error")

        jobs = {
            "A": Job(max_attempts=2)
        }
        results = asyncio.run(execute(jobs, runner))
        self.assertEqual(results["A"].status, "FAILED")
        self.assertIn("Permanent error", results["A"].error)
        self.assertEqual(results["A"].attempts, 2)

    def test_cascading_skip_on_parent_failure(self):
        async def runner(job_id: str, attempt: int):
            if job_id == "A":
                raise RuntimeError("A failed")
            return f"val-{job_id}"

        # A fails -> B depends on A (SKIPPED) -> C depends on B (SKIPPED)
        # D is independent -> SUCCESS
        jobs = {
            "A": Job(max_attempts=1),
            "B": Job(deps=("A",)),
            "C": Job(deps=("B",)),
            "D": Job(),
        }
        results = asyncio.run(execute(jobs, runner))

        self.assertEqual(results["A"].status, "FAILED")
        self.assertEqual(results["B"].status, "SKIPPED")
        self.assertEqual(results["B"].attempts, 0)
        self.assertEqual(results["C"].status, "SKIPPED")
        self.assertEqual(results["C"].attempts, 0)
        self.assertEqual(results["D"].status, "SUCCESS")


if __name__ == "__main__":
    unittest.main()
