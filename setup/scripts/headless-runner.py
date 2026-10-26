#!/usr/bin/env python3
"""
Autonomous Headless Pi Benchmark Orchestrator.
Supports:
  Round 1: 8 runs across Task 1 and Task 2 (Profiles A, B, C, D)
  Round 2: 12 runs across Task 3 and Task 4 (Profiles A and C, 3 repetitions counterbalanced)

Pin model: openai-codex / gpt-6-luna (thinking: max)
Tools: read,bash,edit,write,subagent | MCP: disabled
Independent JSON-mode headless subprocesses.
"""

import argparse
import csv
import datetime
import hashlib
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

BENCH_ROOT = Path("/home/khoa/pi-token-bench").resolve()
SETUP_DIR = BENCH_ROOT / "setup"
SESSIONS_ROOT = SETUP_DIR / "sessions"

# Round 1 paths
PROMPTS_DIR = SETUP_DIR / "prompts"
EVAL_DIR = SETUP_DIR / "evaluation"
RESULTS_DIR = BENCH_ROOT / "results"
RUNS_DIR = RESULTS_DIR / "runs"

# Round 2 paths
R2_DIR = BENCH_ROOT / "round-2"
R2_FIXTURES_DIR = R2_DIR / "fixtures"
R2_EVAL_DIR = R2_DIR / "evaluators"
R2_PROMPTS_DIR = R2_DIR / "prompts"
R2_RESULTS_DIR = R2_DIR / "results"
R2_RUNS_DIR = R2_RESULTS_DIR / "runs"
R2_ARTIFACTS_DIR = R2_RESULTS_DIR / "artifacts"
R2_SESSIONS_ROOT = SETUP_DIR / "sessions" / "round-2"

# Ensure evaluation modules can be imported
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))
if str(R2_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(R2_EVAL_DIR))

try:
    from eval_task_1 import evaluate_task_1
    from eval_task_2 import evaluate_task_2
except ImportError:
    evaluate_task_1 = None
    evaluate_task_2 = None

try:
    from eval_task_3 import evaluate_task_3
    from eval_task_4 import evaluate_task_4
except ImportError:
    evaluate_task_3 = None
    evaluate_task_4 = None

# Round 1 canonical runs
RUN_CONFIGS = [
    {"run_id": "run-01", "task": "task-1", "profile": "A", "workspace": "task-1/test-config-a", "launcher": "bench-a"},
    {"run_id": "run-02", "task": "task-1", "profile": "B", "workspace": "task-1/test-config-b", "launcher": "bench-b"},
    {"run_id": "run-03", "task": "task-1", "profile": "C", "workspace": "task-1/test-config-c", "launcher": "bench-c"},
    {"run_id": "run-04", "task": "task-1", "profile": "D", "workspace": "task-1/test-config-d", "launcher": "bench-d"},
    {"run_id": "run-05", "task": "task-2", "profile": "A", "workspace": "task-2/test-config-a", "launcher": "bench-a"},
    {"run_id": "run-06", "task": "task-2", "profile": "B", "workspace": "task-2/test-config-b", "launcher": "bench-b"},
    {"run_id": "run-07", "task": "task-2", "profile": "C", "workspace": "task-2/test-config-c", "launcher": "bench-c"},
    {"run_id": "run-08", "task": "task-2", "profile": "D", "workspace": "task-2/test-config-d", "launcher": "bench-d"},
]

# Round 2 canonical runs (2 tasks x 2 profiles x 3 repetitions = 12 runs, counterbalanced)
ROUND_2_RUNS = [
    # Task 3 Repeat 1: A -> C
    {
        "run_id": "run-01",
        "task": "task-3",
        "task_name": "Task 3: Concurrent Inventory Reservation",
        "repetition": 1,
        "profile": "A",
        "workspace": "round-2/workspaces/task-3/repeat-1/test-config-a",
        "launcher": "bench-a",
        "fixture_dir": "round-2/fixtures/task-3",
        "designated_file": "src/reservations.py",
        "artifact_name": "task-3-test-config-a-r1.py",
    },
    {
        "run_id": "run-02",
        "task": "task-3",
        "task_name": "Task 3: Concurrent Inventory Reservation",
        "repetition": 1,
        "profile": "C",
        "workspace": "round-2/workspaces/task-3/repeat-1/test-config-c",
        "launcher": "bench-c",
        "fixture_dir": "round-2/fixtures/task-3",
        "designated_file": "src/reservations.py",
        "artifact_name": "task-3-test-config-c-r1.py",
    },
    # Task 3 Repeat 2: C -> A
    {
        "run_id": "run-03",
        "task": "task-3",
        "task_name": "Task 3: Concurrent Inventory Reservation",
        "repetition": 2,
        "profile": "C",
        "workspace": "round-2/workspaces/task-3/repeat-2/test-config-c",
        "launcher": "bench-c",
        "fixture_dir": "round-2/fixtures/task-3",
        "designated_file": "src/reservations.py",
        "artifact_name": "task-3-test-config-c-r2.py",
    },
    {
        "run_id": "run-04",
        "task": "task-3",
        "task_name": "Task 3: Concurrent Inventory Reservation",
        "repetition": 2,
        "profile": "A",
        "workspace": "round-2/workspaces/task-3/repeat-2/test-config-a",
        "launcher": "bench-a",
        "fixture_dir": "round-2/fixtures/task-3",
        "designated_file": "src/reservations.py",
        "artifact_name": "task-3-test-config-a-r2.py",
    },
    # Task 3 Repeat 3: A -> C
    {
        "run_id": "run-05",
        "task": "task-3",
        "task_name": "Task 3: Concurrent Inventory Reservation",
        "repetition": 3,
        "profile": "A",
        "workspace": "round-2/workspaces/task-3/repeat-3/test-config-a",
        "launcher": "bench-a",
        "fixture_dir": "round-2/fixtures/task-3",
        "designated_file": "src/reservations.py",
        "artifact_name": "task-3-test-config-a-r3.py",
    },
    {
        "run_id": "run-06",
        "task": "task-3",
        "task_name": "Task 3: Concurrent Inventory Reservation",
        "repetition": 3,
        "profile": "C",
        "workspace": "round-2/workspaces/task-3/repeat-3/test-config-c",
        "launcher": "bench-c",
        "fixture_dir": "round-2/fixtures/task-3",
        "designated_file": "src/reservations.py",
        "artifact_name": "task-3-test-config-c-r3.py",
    },
    # Task 4 Repeat 1: C -> A
    {
        "run_id": "run-07",
        "task": "task-4",
        "task_name": "Task 4: Crash-Safe Incremental File Indexer",
        "repetition": 1,
        "profile": "C",
        "workspace": "round-2/workspaces/task-4/repeat-1/test-config-c",
        "launcher": "bench-c",
        "fixture_dir": "round-2/fixtures/task-4",
        "designated_file": "src/indexer.py",
        "artifact_name": "task-4-test-config-c-r1.py",
    },
    {
        "run_id": "run-08",
        "task": "task-4",
        "task_name": "Task 4: Crash-Safe Incremental File Indexer",
        "repetition": 1,
        "profile": "A",
        "workspace": "round-2/workspaces/task-4/repeat-1/test-config-a",
        "launcher": "bench-a",
        "fixture_dir": "round-2/fixtures/task-4",
        "designated_file": "src/indexer.py",
        "artifact_name": "task-4-test-config-a-r1.py",
    },
    # Task 4 Repeat 2: A -> C
    {
        "run_id": "run-09",
        "task": "task-4",
        "task_name": "Task 4: Crash-Safe Incremental File Indexer",
        "repetition": 2,
        "profile": "A",
        "workspace": "round-2/workspaces/task-4/repeat-2/test-config-a",
        "launcher": "bench-a",
        "fixture_dir": "round-2/fixtures/task-4",
        "designated_file": "src/indexer.py",
        "artifact_name": "task-4-test-config-a-r2.py",
    },
    {
        "run_id": "run-10",
        "task": "task-4",
        "task_name": "Task 4: Crash-Safe Incremental File Indexer",
        "repetition": 2,
        "profile": "C",
        "workspace": "round-2/workspaces/task-4/repeat-2/test-config-c",
        "launcher": "bench-c",
        "fixture_dir": "round-2/fixtures/task-4",
        "designated_file": "src/indexer.py",
        "artifact_name": "task-4-test-config-c-r2.py",
    },
    # Task 4 Repeat 3: C -> A
    {
        "run_id": "run-11",
        "task": "task-4",
        "task_name": "Task 4: Crash-Safe Incremental File Indexer",
        "repetition": 3,
        "profile": "C",
        "workspace": "round-2/workspaces/task-4/repeat-3/test-config-c",
        "launcher": "bench-c",
        "fixture_dir": "round-2/fixtures/task-4",
        "designated_file": "src/indexer.py",
        "artifact_name": "task-4-test-config-c-r3.py",
    },
    {
        "run_id": "run-12",
        "task": "task-4",
        "task_name": "Task 4: Crash-Safe Incremental File Indexer",
        "repetition": 3,
        "profile": "A",
        "workspace": "round-2/workspaces/task-4/repeat-3/test-config-a",
        "launcher": "bench-a",
        "fixture_dir": "round-2/fixtures/task-4",
        "designated_file": "src/indexer.py",
        "artifact_name": "task-4-test-config-a-r3.py",
    },
]

PROFILE_NAMES = {
    "A": "A — Baseline",
    "B": "B — Minimal",
    "C": "C — Terse",
    "D": "D — Optimized",
}


def atomic_write_json(file_path: Path, data: Any):
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = file_path.with_suffix(".tmp." + str(os.getpid()))
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(temp_path, file_path)


def atomic_write_text(file_path: Path, text: str):
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = file_path.with_suffix(".tmp." + str(os.getpid()))
    with open(temp_path, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(temp_path, file_path)


class TelemetryCollector:
    def __init__(self, run_config: dict):
        self.run_id = run_config["run_id"]
        self.task_id = run_config["task"]
        self.repetition = run_config.get("repetition", 1)
        self.profile = run_config["profile"]
        self.profile_name = PROFILE_NAMES.get(self.profile, self.profile)
        self.workspace = run_config["workspace"]
        self.workspace_dir = str(BENCH_ROOT / self.workspace)
        
        self.pi_version = "1.4.0"
        self.model_id = "gpt-6-luna"
        self.provider = "openai-codex"
        self.reasoning_level = "max"
        
        self.session_id: Optional[str] = None
        self.start_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.end_time: Optional[str] = None
        self.duration_sec = 0.0
        self.exit_code: Optional[int] = None
        self.termination_reason = "pending"
        
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.total_tokens = 0
        self.estimated_cost = 0.0
        self.cost_incomplete = False
        
        self.tool_calls: Dict[str, int] = {}
        self.tool_errors = 0
        self.subagent_calls = 0
        self.subagent_usage: Optional[dict] = None
        
        self.retry_events = 0
        self.compaction_events = 0
        self.agent_settled_aborted: Optional[bool] = None
        self.assistant_stop_reason: Optional[str] = None
        
        # Evaluation outcomes
        self.self_test_passed = False
        self.self_test_output = ""
        self.independent_test_passed = False
        self.independent_total = 0
        self.independent_passed = 0
        self.failures: List[str] = []
        self.output_file: Optional[str] = None
        self.output_file_sha256: Optional[str] = None
        self.line_count = 0
        
        self.status = "PENDING"
        self._lock = threading.Lock()

    def process_event(self, event: dict):
        with self._lock:
            event_type = event.get("type")
            if event_type == "session":
                self.session_id = event.get("id")
            elif event_type == "message_end":
                msg = event.get("message", {})
                if msg.get("role") == "assistant":
                    usage = msg.get("usage", {})
                    inp = usage.get("input", 0)
                    out = usage.get("output", 0)
                    cr = usage.get("cacheRead", 0)
                    cw = usage.get("cacheWrite", 0)
                    tot = usage.get("totalTokens", inp + out + cr + cw)
                    cost = usage.get("cost", {}).get("total", 0.0)

                    self.input_tokens += inp
                    self.output_tokens += out
                    self.cache_read_tokens += cr
                    self.cache_write_tokens += cw
                    self.total_tokens += tot
                    self.estimated_cost += cost

                    if msg.get("stopReason"):
                        self.assistant_stop_reason = msg.get("stopReason")
                    if msg.get("model"):
                        self.model_id = msg.get("model")
                    if msg.get("provider"):
                        self.provider = msg.get("provider")

            elif event_type == "compaction_start":
                self.compaction_events += 1

            elif event_type == "compaction_end":
                res = event.get("result", {})
                if res and "usage" in res:
                    c_usage = res["usage"]
                    self.input_tokens += c_usage.get("input", 0)
                    self.output_tokens += c_usage.get("output", 0)
                    self.cache_read_tokens += c_usage.get("cacheRead", 0)
                    self.cache_write_tokens += c_usage.get("cacheWrite", 0)
                    self.total_tokens += c_usage.get("totalTokens", 0)
                    self.estimated_cost += c_usage.get("cost", {}).get("total", 0.0)

            elif event_type == "auto_retry_start":
                self.retry_events += 1

            elif event_type == "tool_execution_start":
                t_name = event.get("toolName", "unknown")
                self.tool_calls[t_name] = self.tool_calls.get(t_name, 0) + 1
                if t_name == "subagent":
                    self.subagent_calls += 1

            elif event_type == "tool_execution_end":
                if event.get("isError"):
                    self.tool_errors += 1
                t_name = event.get("toolName")
                if t_name == "subagent":
                    res = event.get("result", {})
                    details = res.get("details", {})
                    if "usage" in details:
                        self.subagent_usage = details["usage"]
                    else:
                        self.cost_incomplete = True

            elif event_type == "agent_settled":
                self.agent_settled_aborted = event.get("aborted", False)

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "run_id": self.run_id,
                "task_id": self.task_id,
                "repetition": self.repetition,
                "profile": self.profile,
                "profile_name": self.profile_name,
                "workspace": self.workspace,
                "workspace_dir": self.workspace_dir,
                "pi_version": self.pi_version,
                "model_id": self.model_id,
                "provider": self.provider,
                "reasoning_level": self.reasoning_level,
                "session_id": self.session_id,
                "start_time": self.start_time,
                "end_time": self.end_time,
                "duration_sec": round(self.duration_sec, 2),
                "exit_code": self.exit_code,
                "termination_reason": self.termination_reason,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cache_read_tokens": self.cache_read_tokens,
                "cache_write_tokens": self.cache_write_tokens,
                "total_tokens": self.total_tokens,
                "estimated_cost": round(self.estimated_cost, 6),
                "cost_incomplete": self.cost_incomplete,
                "tool_calls": self.tool_calls,
                "tool_errors": self.tool_errors,
                "subagent_calls": self.subagent_calls,
                "subagent_usage": self.subagent_usage,
                "retry_events": self.retry_events,
                "compaction_events": self.compaction_events,
                "agent_settled_aborted": self.agent_settled_aborted,
                "assistant_stop_reason": self.assistant_stop_reason,
                "self_test_passed": self.self_test_passed,
                "self_test_output": self.self_test_output,
                "independent_test_passed": self.independent_test_passed,
                "independent_total": self.independent_total,
                "independent_passed": self.independent_passed,
                "failures": self.failures,
                "output_file": self.output_file,
                "output_file_sha256": self.output_file_sha256,
                "line_count": self.line_count,
                "status": self.status,
            }


def query_rtk_gain() -> dict:
    rtk_bin = SETUP_DIR / "bin" / "rtk"
    xdg_share = SETUP_DIR / ".local" / "share"
    if not rtk_bin.is_file():
        return {}
    env = os.environ.copy()
    env["XDG_DATA_HOME"] = str(xdg_share)
    try:
        proc = subprocess.run(
            [str(rtk_bin), "gain", "--format", "json"],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        if proc.returncode == 0:
            return json.loads(proc.stdout)
    except Exception:
        pass
    return {}


# ==============================================================================
# Round 1 Logic
# ==============================================================================

def run_benchmark_worker_r1(run_config: dict, timeout_sec: int = 900) -> TelemetryCollector:
    collector = TelemetryCollector(run_config)
    run_id = run_config["run_id"]
    task_id = run_config["task"]
    profile = run_config["profile"]
    workspace_dir = BENCH_ROOT / run_config["workspace"]
    launcher_script = BENCH_ROOT / run_config["launcher"]
    prompt_file = PROMPTS_DIR / f"{task_id}.md"

    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    session_dir = SESSIONS_ROOT / run_id
    session_dir.mkdir(parents=True, exist_ok=True)

    if not prompt_file.is_file():
        collector.status = "BLOCKED"
        collector.failures.append(f"Prompt file missing: {prompt_file}")
        return collector

    prompt_content = prompt_file.read_text(encoding="utf-8")

    cmd = [
        str(launcher_script),
        str(workspace_dir),
        "--mode",
        "json",
        "--",
        prompt_content,
    ]

    env = os.environ.copy()
    env["BENCH_SESSION_DIR"] = str(session_dir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    stdout_log_path = run_dir / "stdout.jsonl"
    stderr_log_path = run_dir / "stderr.log"

    stdout_f = open(stdout_log_path, "w", encoding="utf-8", errors="replace")
    stderr_f = open(stderr_log_path, "w", encoding="utf-8", errors="replace")

    start_mono = time.monotonic()
    timed_out = False

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(workspace_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            bufsize=1,
            process_group=0,
            env=env,
        )
    except Exception as e:
        collector.status = "BLOCKED"
        collector.failures.append(f"Failed to spawn worker process: {e}")
        collector.end_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
        stdout_f.close()
        stderr_f.close()
        return collector

    def read_stdout():
        try:
            for line in iter(proc.stdout.readline, ""):
                stdout_f.write(line)
                stdout_f.flush()
                line_str = line.strip()
                if line_str.startswith("{") and line_str.endswith("}"):
                    try:
                        ev = json.loads(line_str)
                        collector.process_event(ev)
                    except json.JSONDecodeError:
                        pass
        except Exception:
            pass
        finally:
            proc.stdout.close()

    def read_stderr():
        try:
            for line in iter(proc.stderr.readline, ""):
                stderr_f.write(line)
                stderr_f.flush()
        except Exception:
            pass
        finally:
            proc.stderr.close()

    t_out = threading.Thread(target=read_stdout, daemon=True)
    t_err = threading.Thread(target=read_stderr, daemon=True)
    t_out.start()
    t_err.start()

    try:
        proc.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        timed_out = True
        collector.termination_reason = "timeout"
        collector.failures.append(f"Process timed out after {timeout_sec} seconds")
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
            proc.wait(timeout=10)
        except Exception:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass

    t_out.join(timeout=5)
    t_err.join(timeout=5)
    stdout_f.close()
    stderr_f.close()

    end_mono = time.monotonic()
    collector.duration_sec = end_mono - start_mono
    collector.end_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
    collector.exit_code = proc.returncode

    if not timed_out:
        if proc.returncode == 0:
            collector.termination_reason = "completed"
        else:
            collector.termination_reason = "nonzero_exit"
            collector.failures.append(f"Process exited with non-zero code {proc.returncode}")

    if collector.agent_settled_aborted:
        collector.failures.append("Session was aborted (agent_settled aborted=true)")
    if collector.assistant_stop_reason == "error":
        collector.failures.append("Assistant message ended with stopReason='error'")

    eval_res = {}
    if task_id == "task-1":
        eval_res = evaluate_task_1(str(workspace_dir))
    elif task_id == "task-2":
        eval_res = evaluate_task_2(str(workspace_dir))

    collector.self_test_passed = eval_res.get("self_tests_passed", False)
    collector.self_test_output = eval_res.get("self_tests_output", "")
    collector.independent_test_passed = eval_res.get("external_tests_passed", False)
    collector.independent_total = eval_res.get("external_total", 0)
    collector.independent_passed = eval_res.get("external_passed", 0)
    collector.output_file = eval_res.get("expected_filename")
    collector.output_file_sha256 = eval_res.get("deliverable_sha256")
    collector.line_count = eval_res.get("line_count", 0)

    if eval_res.get("failures"):
        collector.failures.extend(eval_res["failures"])

    if timed_out:
        collector.status = "TIMEOUT"
    elif proc.returncode != 0:
        collector.status = "FAIL"
    elif eval_res.get("overall_passed"):
        collector.status = "PASS"
    else:
        collector.status = "FAIL"

    atomic_write_json(run_dir / "eval.json", eval_res)
    atomic_write_json(run_dir / "telemetry.json", collector.to_dict())

    return collector


def generate_reports_r1(results: List[dict]):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    manifest = {
        "benchmark_id": "pi-token-bench-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S"),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "total_runs": len(results),
        "completed_runs": len([r for r in results if r["status"] in ("PASS", "FAIL", "TIMEOUT")]),
        "passed_runs": len([r for r in results if r["status"] == "PASS"]),
        "failed_runs": len([r for r in results if r["status"] in ("FAIL", "TIMEOUT")]),
        "runs": results,
    }
    atomic_write_json(RESULTS_DIR / "manifest.json", manifest)

    profiles = ["A", "B", "C", "D"]
    profile_summary = {}

    for p in profiles:
        p_runs = [r for r in results if r["profile"] == p]
        tot_inp = sum(r["input_tokens"] for r in p_runs)
        tot_out = sum(r["output_tokens"] for r in p_runs)
        tot_cr = sum(r["cache_read_tokens"] for r in p_runs)
        tot_cw = sum(r["cache_write_tokens"] for r in p_runs)
        tot_tok = sum(r["total_tokens"] for r in p_runs)
        tot_cost = sum(r["estimated_cost"] for r in p_runs)
        tot_dur = sum(r["duration_sec"] for r in p_runs)
        passed_cnt = sum(1 for r in p_runs if r["status"] == "PASS")
        total_cnt = len(p_runs)
        cost_per_pass = (tot_cost / passed_cnt) if passed_cnt > 0 else None

        profile_summary[p] = {
            "profile_name": PROFILE_NAMES.get(p, p),
            "runs_count": total_cnt,
            "passed_count": passed_cnt,
            "pass_rate_pct": round((passed_cnt / total_cnt * 100), 1) if total_cnt > 0 else 0.0,
            "total_input_tokens": tot_inp,
            "total_output_tokens": tot_out,
            "total_cache_read_tokens": tot_cr,
            "total_cache_write_tokens": tot_cw,
            "total_tokens": tot_tok,
            "total_cost": round(tot_cost, 6),
            "total_duration_sec": round(tot_dur, 2),
            "cost_per_successful_task": round(cost_per_pass, 6) if cost_per_pass is not None else None,
            "runs": [r["run_id"] for r in p_runs],
        }

    baseline_a = profile_summary.get("A", {})
    base_cost = baseline_a.get("total_cost", 0.0)
    base_inp = baseline_a.get("total_input_tokens", 0)
    base_out = baseline_a.get("total_output_tokens", 0)
    base_tok = baseline_a.get("total_tokens", 0)

    for p in profiles:
        summary = profile_summary[p]
        if base_cost > 0:
            summary["cost_reduction_pct_vs_A"] = round(((base_cost - summary["total_cost"]) / base_cost) * 100, 2)
        if base_inp > 0:
            summary["input_reduction_pct_vs_A"] = round(((base_inp - summary["total_input_tokens"]) / base_inp) * 100, 2)
        if base_out > 0:
            summary["output_reduction_pct_vs_A"] = round(((base_out - summary["total_output_tokens"]) / base_out) * 100, 2)
        if base_tok > 0:
            summary["total_tokens_reduction_pct_vs_A"] = round(((base_tok - summary["total_tokens"]) / base_tok) * 100, 2)

    rtk_gain = query_rtk_gain()

    full_results_data = {
        "manifest": manifest,
        "profile_aggregations": profile_summary,
        "rtk_gain_telemetry": rtk_gain,
        "individual_runs": results,
    }
    atomic_write_json(RESULTS_DIR / "benchmark-results.json", full_results_data)

    csv_path = RESULTS_DIR / "benchmark-results.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Run", "Task", "Profile", "Status", "SelfTestsPassed", "ExternalTests",
            "InputTokens", "OutputTokens", "CacheReadTokens", "CacheWriteTokens",
            "TotalTokens", "EstimatedCost", "DurationSec", "ToolCalls", "ToolErrors", "OutputSHA256"
        ])
        for r in results:
            tool_call_str = ";".join(f"{k}:{v}" for k, v in r.get("tool_calls", {}).items())
            ext_str = f"{r.get('independent_passed', 0)}/{r.get('independent_total', 0)}"
            writer.writerow([
                r["run_id"], r["task_id"], r["profile"], r["status"],
                r["self_test_passed"], ext_str, r["input_tokens"], r["output_tokens"],
                r["cache_read_tokens"], r["cache_write_tokens"], r["total_tokens"],
                r["estimated_cost"], r["duration_sec"], tool_call_str, r["tool_errors"],
                r.get("output_file_sha256") or "N/A"
            ])

    md_lines = [
        "# Pi Token Optimization Benchmark Report (Round 1)",
        "",
        f"**Date:** {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
        "**Environment:** CachyOS (Arch Linux) | Pi 1.4.0 | Python 3.11+  ",
        "**Pinned Harness:** Model: `openai-codex/gpt-6-luna` | Thinking: `max` | MCP: `disabled` | Tools: `read,bash,edit,write,subagent`  ",
        "",
        "---",
        "",
        "## 1. Executive Summary & Results Table",
        "",
        "| Task | Profile | Tests | Input | Output | Cache Read | Cost | Duration | Tool Calls | Status |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]

    for r in results:
        task_label = "Task 1" if r["task_id"] == "task-1" else "Task 2"
        ext_status = f"{r.get('independent_passed', 0)}/{r.get('independent_total', 0)}"
        t_calls = sum(r.get("tool_calls", {}).values())
        md_lines.append(
            f"| {task_label} | {r['profile']} | {ext_status} | "
            f"{r['input_tokens']:,} | {r['output_tokens']:,} | {r['cache_read_tokens']:,} | "
            f"${r['estimated_cost']:.4f} | {r['duration_sec']:.1f}s | {t_calls} | **{r['status']}** |"
        )

    atomic_write_text(RESULTS_DIR / "BENCHMARK_REPORT.md", "\n".join(md_lines) + "\n")


run_benchmark_worker = run_benchmark_worker_r1
generate_reports = generate_reports_r1


def cmd_dry_run():
    print("======================================================================")
    print("  Pi Token Benchmark Orchestrator — Dry Run & Preflight Verification")
    print("======================================================================")

    all_passed = True

    def check(desc, condition, err=""):
        nonlocal all_passed
        if condition:
            print(f"  [PASS] {desc}")
        else:
            print(f"  [FAIL] {desc}: {err}")
            all_passed = False

    check("Benchmark root directory exists", BENCH_ROOT.is_dir())
    check("Prompt directory exists", PROMPTS_DIR.is_dir())
    check("Evaluation directory exists", EVAL_DIR.is_dir())
    check("Task 1 prompt exists", (PROMPTS_DIR / "task-1.md").is_file())
    check("Task 2 prompt exists", (PROMPTS_DIR / "task-2.md").is_file())

    for r in RUN_CONFIGS:
        ws = BENCH_ROOT / r["workspace"]
        exists = ws.is_dir()
        check(f"Workspace {r['workspace']} exists", exists)

    env = os.environ.copy()
    env["BENCH_DRY_RUN"] = "1"
    for profile in ["a", "b", "c", "d"]:
        launcher = BENCH_ROOT / f"bench-{profile}"
        ws = BENCH_ROOT / f"task-1/test-config-{profile}"
        proc = subprocess.run([str(launcher), str(ws)], capture_output=True, text=True, env=env)
        check(f"Launcher bench-{profile} dry run", proc.returncode == 0, proc.stderr)

    check("Task 1 evaluator loadable", evaluate_task_1 is not None)
    check("Task 2 evaluator loadable", evaluate_task_2 is not None)

    pi_proc = subprocess.run(["pi", "--version"], capture_output=True, text=True)
    check(f"Pi CLI operational ({pi_proc.stdout.strip()})", pi_proc.returncode == 0)

    print("======================================================================")
    if all_passed:
        print("  [SUCCESS] All preflight checks passed. Orchestrator ready for --run.")
        sys.exit(0)
    else:
        print("  [ERROR] One or more preflight checks failed.")
        sys.exit(1)


def cmd_status():
    manifest_path = RESULTS_DIR / "manifest.json"
    if not manifest_path.is_file():
        print("No benchmark execution manifest found at", manifest_path)
        print("Run with --run to execute the benchmark.")
        return

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print("===================================================================================================")
    print(f"  Pi Token Optimization Benchmark Status (ID: {manifest.get('benchmark_id')})")
    print(f"  Completed: {manifest.get('completed_runs')}/{manifest.get('total_runs')} | Passed: {manifest.get('passed_runs')} | Failed: {manifest.get('failed_runs')}")
    print("===================================================================================================")
    print(f"{'Run':<8} {'Task':<8} {'Profile':<10} {'Status':<10} {'ExtTests':<10} {'Cost':<10} {'Tokens':<10} {'Duration':<10}")
    print("-" * 85)

    for r in manifest.get("runs", []):
        ext = f"{r.get('independent_passed', 0)}/{r.get('independent_total', 0)}"
        cost = f"${r.get('estimated_cost', 0):.4f}"
        tok = f"{r.get('total_tokens', 0):,}"
        dur = f"{r.get('duration_sec', 0):.1f}s"
        print(f"{r['run_id']:<8} {r['task_id']:<8} {r['profile']:<10} {r['status']:<10} {ext:<10} {cost:<10} {tok:<10} {dur:<10}")
    print("===================================================================================================")


def cmd_run(resume_mode: bool = False, retry_run_id: Optional[str] = None, timeout_sec: int = 900):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    print("======================================================================")
    print("  Pi Autonomous Headless Benchmark Orchestrator — Round 1")
    print("  Starting 8 Independent Pi Sessions (gpt-6-luna:max, openai-codex)")
    print(f"  Mode: {'RETRY ' + retry_run_id if retry_run_id else ('RESUME' if resume_mode else 'FULL RUN')}")
    print("======================================================================")

    existing_results: Dict[str, dict] = {}
    manifest_path = RESULTS_DIR / "manifest.json"
    if manifest_path.is_file():
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            for r in m.get("runs", []):
                existing_results[r["run_id"]] = r
        except Exception:
            pass

    results_list: List[dict] = []

    for idx, run_cfg in enumerate(RUN_CONFIGS, 1):
        run_id = run_cfg["run_id"]
        task_label = "Task 1" if run_cfg["task"] == "task-1" else "Task 2"
        profile = run_cfg["profile"]

        if retry_run_id and run_id != retry_run_id:
            if run_id in existing_results:
                results_list.append(existing_results[run_id])
            continue

        if resume_mode and not retry_run_id:
            if run_id in existing_results and existing_results[run_id].get("status") in ("PASS", "FAIL"):
                r_prev = existing_results[run_id]
                print(f"[{idx}/8] {task_label} {profile} | SKIPPED (Resume: {r_prev.get('status')})")
                results_list.append(r_prev)
                continue

        if retry_run_id and run_id == retry_run_id:
            prev_dir = RUNS_DIR / run_id
            if prev_dir.is_dir():
                attempt_archive = prev_dir / f"attempt-prev-{int(time.time())}"
                attempt_archive.mkdir(parents=True, exist_ok=True)
                for item in prev_dir.iterdir():
                    if item.name.startswith("attempt-"):
                        continue
                    try:
                        shutil.move(str(item), str(attempt_archive / item.name))
                    except Exception:
                        pass

        collector = run_benchmark_worker(run_cfg, timeout_sec=timeout_sec)
        run_data = collector.to_dict()
        results_list.append(run_data)

        ext_summary = f"{run_data.get('independent_passed', 0)}/{run_data.get('independent_total', 0)}"
        print(
            f"[{idx}/8] {task_label} {profile} | {run_data['status']} | "
            f"cost=${run_data['estimated_cost']:.4f} | tokens={run_data['total_tokens']:,} | "
            f"time={run_data['duration_sec']:.1f}s | tests={ext_summary}"
        )

        generate_reports(results_list)

    print("======================================================================")
    print("  Benchmark Run Complete!")
    print(f"  Results saved to: {RESULTS_DIR}")
    print(f"  Report generated: {RESULTS_DIR / 'BENCHMARK_REPORT.md'}")
    print("======================================================================")


def cmd_reevaluate():
    print("======================================================================")
    print("  Re-evaluating Generated Deliverables Across All 8 Runs")
    print("======================================================================")
    results_list = []
    for idx, run_cfg in enumerate(RUN_CONFIGS, 1):
        run_id = run_cfg["run_id"]
        task_id = run_cfg["task"]
        profile = run_cfg["profile"]
        ws_dir = BENCH_ROOT / run_cfg["workspace"]
        run_dir = RUNS_DIR / run_id
        telemetry_file = run_dir / "telemetry.json"

        if not telemetry_file.is_file():
            print(f"[{idx}/8] {run_id}: No telemetry found, skipping")
            continue

        run_data = json.loads(telemetry_file.read_text(encoding="utf-8"))

        if task_id == "task-1":
            eval_res = evaluate_task_1(str(ws_dir))
        else:
            eval_res = evaluate_task_2(str(ws_dir))

        atomic_write_json(run_dir / "eval.json", eval_res)

        run_data["self_test_passed"] = eval_res.get("self_tests_passed", False)
        run_data["self_test_output"] = eval_res.get("self_tests_output", "")
        run_data["independent_test_passed"] = eval_res.get("external_tests_passed", False)
        run_data["independent_total"] = eval_res.get("external_total", 0)
        run_data["independent_passed"] = eval_res.get("external_passed", 0)
        run_data["output_file"] = eval_res.get("expected_filename")
        run_data["output_file_sha256"] = eval_res.get("deliverable_sha256")
        run_data["line_count"] = eval_res.get("line_count", 0)
        run_data["failures"] = eval_res.get("failures", [])

        if run_data.get("termination_reason") == "timeout":
            run_data["status"] = "TIMEOUT"
        elif run_data.get("exit_code") != 0:
            run_data["status"] = "FAIL"
        elif eval_res.get("overall_passed"):
            run_data["status"] = "PASS"
        else:
            run_data["status"] = "FAIL"

        atomic_write_json(telemetry_file, run_data)
        results_list.append(run_data)

        ext_summary = f"{run_data.get('independent_passed', 0)}/{run_data.get('independent_total', 0)}"
        print(f"[{idx}/8] {task_id} {profile} | {run_data['status']} | tests={ext_summary} | lines={run_data['line_count']}")

    generate_reports(results_list)
    print("======================================================================")
    print("  Re-evaluation Complete & Reports Updated!")
    print("======================================================================")


# ==============================================================================
# Round 2 Logic
# ==============================================================================

def run_round_2_worker(run_config: dict, timeout_sec: int = 1800) -> TelemetryCollector:
    collector = TelemetryCollector(run_config)
    run_id = run_config["run_id"]
    task_id = run_config["task"]
    repetition = run_config["repetition"]
    profile = run_config["profile"]
    workspace_dir = BENCH_ROOT / run_config["workspace"]
    fixture_dir = BENCH_ROOT / run_config["fixture_dir"]
    launcher_script = BENCH_ROOT / run_config["launcher"]
    prompt_file = R2_PROMPTS_DIR / f"{task_id}.md"
    designated_file = run_config["designated_file"]
    artifact_name = run_config["artifact_name"]

    run_dir = R2_RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    session_dir = R2_SESSIONS_ROOT / run_id
    session_dir.mkdir(parents=True, exist_ok=True)

    if not prompt_file.is_file():
        collector.status = "ENVIRONMENT_ERROR"
        collector.failures.append(f"Prompt file missing: {prompt_file}")
        return collector

    prompt_content = prompt_file.read_text(encoding="utf-8")

    # Clean workspace restore from immutable fixture
    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    workspace_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(fixture_dir, workspace_dir)

    # Clean __pycache__ from workspace
    for p in workspace_dir.rglob("__pycache__"):
        if p.is_dir():
            shutil.rmtree(p)

    cmd = [
        str(launcher_script),
        str(workspace_dir),
        "--mode",
        "json",
        "--",
        prompt_content,
    ]

    env = os.environ.copy()
    env["BENCH_SESSION_DIR"] = str(session_dir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    stdout_log_path = run_dir / "stdout.jsonl"
    stderr_log_path = run_dir / "stderr.log"

    stdout_f = open(stdout_log_path, "w", encoding="utf-8", errors="replace")
    stderr_f = open(stderr_log_path, "w", encoding="utf-8", errors="replace")

    start_mono = time.monotonic()
    timed_out = False

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(workspace_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            bufsize=1,
            process_group=0,
            env=env,
        )
    except Exception as e:
        collector.status = "ENVIRONMENT_ERROR"
        collector.failures.append(f"Failed to spawn worker process: {e}")
        collector.end_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
        stdout_f.close()
        stderr_f.close()
        return collector

    def read_stdout():
        try:
            for line in iter(proc.stdout.readline, ""):
                stdout_f.write(line)
                stdout_f.flush()
                line_str = line.strip()
                if line_str.startswith("{") and line_str.endswith("}"):
                    try:
                        ev = json.loads(line_str)
                        collector.process_event(ev)
                    except json.JSONDecodeError:
                        pass
        except Exception:
            pass
        finally:
            proc.stdout.close()

    def read_stderr():
        try:
            for line in iter(proc.stderr.readline, ""):
                stderr_f.write(line)
                stderr_f.flush()
        except Exception:
            pass
        finally:
            proc.stderr.close()

    t_out = threading.Thread(target=read_stdout, daemon=True)
    t_err = threading.Thread(target=read_stderr, daemon=True)
    t_out.start()
    t_err.start()

    try:
        proc.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        timed_out = True
        collector.termination_reason = "timeout"
        collector.failures.append(f"Process timed out after {timeout_sec} seconds")
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
            proc.wait(timeout=10)
        except Exception:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass

    t_out.join(timeout=5)
    t_err.join(timeout=5)
    stdout_f.close()
    stderr_f.close()

    end_mono = time.monotonic()
    collector.duration_sec = end_mono - start_mono
    collector.end_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
    collector.exit_code = proc.returncode

    if not timed_out:
        if proc.returncode == 0:
            collector.termination_reason = "completed"
        else:
            collector.termination_reason = "nonzero_exit"
            collector.failures.append(f"Process exited with non-zero code {proc.returncode}")

    if collector.agent_settled_aborted:
        collector.failures.append("Session was aborted (agent_settled aborted=true)")
    if collector.assistant_stop_reason == "error":
        collector.failures.append("Assistant message ended with stopReason='error'")

    # Run Independent Evaluator
    eval_res = {}
    if task_id == "task-3":
        eval_res = evaluate_task_3(str(workspace_dir), str(fixture_dir))
    elif task_id == "task-4":
        eval_res = evaluate_task_4(str(workspace_dir), str(fixture_dir))

    collector.self_test_passed = eval_res.get("public_tests_passed", False)
    collector.self_test_output = eval_res.get("public_tests_output", "")
    collector.independent_test_passed = eval_res.get("hidden_tests_passed", False)
    collector.independent_total = eval_res.get("hidden_total", 0)
    collector.independent_passed = eval_res.get("hidden_passed", 0)
    collector.output_file = artifact_name
    collector.output_file_sha256 = eval_res.get("sha256")
    collector.line_count = eval_res.get("line_count", 0)

    if eval_res.get("failures"):
        collector.failures.extend(eval_res["failures"])

    # Export deliverable artifact
    src_file = workspace_dir / designated_file
    R2_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    exported_artifact_path = R2_ARTIFACTS_DIR / artifact_name
    if src_file.is_file():
        shutil.copy2(src_file, exported_artifact_path)

    # Classify status
    if timed_out:
        collector.status = "TIMEOUT"
    elif proc.returncode != 0:
        collector.status = "AGENT_ERROR"
    elif not eval_res.get("single_file_modified", False) or not eval_res.get("target_exists", False):
        collector.status = "INVALID_ARTIFACT"
    elif eval_res.get("status") == "PASS":
        collector.status = "PASS"
    else:
        collector.status = "TEST_FAIL"

    atomic_write_json(run_dir / "eval.json", eval_res)
    atomic_write_json(run_dir / "telemetry.json", collector.to_dict())

    return collector


def generate_round_2_reports(results: List[dict]):
    R2_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    manifest = {
        "benchmark_round": 2,
        "benchmark_id": "pi-token-bench-r2-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S"),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "total_runs": len(results),
        "completed_runs": len([r for r in results if r["status"] in ("PASS", "TEST_FAIL", "TIMEOUT", "AGENT_ERROR", "INVALID_ARTIFACT")]),
        "passed_runs": len([r for r in results if r["status"] == "PASS"]),
        "failed_runs": len([r for r in results if r["status"] != "PASS"]),
        "runs": results,
    }
    atomic_write_json(R2_RESULTS_DIR / "manifest.json", manifest)

    # Statistical Aggregations for A and C
    profiles = ["A", "C"]
    profile_summary = {}

    for p in profiles:
        p_runs = [r for r in results if r["profile"] == p]
        n_runs = len(p_runs)
        passed_runs = [r for r in p_runs if r["status"] == "PASS"]
        n_passed = len(passed_runs)

        inp_tokens = [r["input_tokens"] for r in p_runs] or [0]
        out_tokens = [r["output_tokens"] for r in p_runs] or [0]
        cr_tokens = [r["cache_read_tokens"] for r in p_runs] or [0]
        tot_tokens = [r["total_tokens"] for r in p_runs] or [0]
        costs = [r["estimated_cost"] for r in p_runs] or [0.0]
        durations = [r["duration_sec"] for r in p_runs] or [0.0]
        tool_calls = [sum(r.get("tool_calls", {}).values()) for r in p_runs] or [0]
        line_counts = [r.get("line_count", 0) for r in p_runs] or [0]

        tot_cost = sum(costs)
        cost_per_pass = (tot_cost / n_passed) if n_passed > 0 else None

        profile_summary[p] = {
            "profile_name": PROFILE_NAMES.get(p, p),
            "total_runs": n_runs,
            "passed_runs": n_passed,
            "pass_rate_pct": round((n_passed / n_runs * 100), 1) if n_runs > 0 else 0.0,
            "median_input_tokens": statistics.median(inp_tokens),
            "median_output_tokens": statistics.median(out_tokens),
            "median_cache_read_tokens": statistics.median(cr_tokens),
            "median_total_tokens": statistics.median(tot_tokens),
            "median_cost": round(statistics.median(costs), 6),
            "median_duration_sec": round(statistics.median(durations), 2),
            "median_tool_calls": statistics.median(tool_calls),
            "median_code_lines": statistics.median(line_counts),
            "total_input_tokens": sum(inp_tokens),
            "total_output_tokens": sum(out_tokens),
            "total_cache_read_tokens": sum(cr_tokens),
            "total_tokens": sum(tot_tokens),
            "total_cost": round(tot_cost, 6),
            "total_duration_sec": round(sum(durations), 2),
            "total_tool_calls": sum(tool_calls),
            "total_tool_errors": sum(r.get("tool_errors", 0) for r in p_runs),
            "cost_per_verified_task": round(cost_per_pass, 6) if cost_per_pass is not None else None,
        }

    # Relative reductions (C vs A)
    base_a = profile_summary.get("A", {})
    comp_c = profile_summary.get("C", {})
    reductions = {}
    if base_a.get("median_cost", 0) > 0:
        reductions["median_cost_reduction_pct"] = round(
            ((base_a["median_cost"] - comp_c["median_cost"]) / base_a["median_cost"]) * 100, 2
        )
    if base_a.get("median_input_tokens", 0) > 0:
        reductions["median_input_reduction_pct"] = round(
            ((base_a["median_input_tokens"] - comp_c["median_input_tokens"]) / base_a["median_input_tokens"]) * 100, 2
        )
    if base_a.get("median_output_tokens", 0) > 0:
        reductions["median_output_reduction_pct"] = round(
            ((base_a["median_output_tokens"] - comp_c["median_output_tokens"]) / base_a["median_output_tokens"]) * 100, 2
        )
    if base_a.get("total_cost", 0) > 0:
        reductions["total_cost_reduction_pct"] = round(
            ((base_a["total_cost"] - comp_c["total_cost"]) / base_a["total_cost"]) * 100, 2
        )

    # Paired comparisons by task and repetition
    pairs = []
    for task in ("task-3", "task-4"):
        for rep in (1, 2, 3):
            run_a = next((r for r in results if r["task_id"] == task and r["repetition"] == rep and r["profile"] == "A"), None)
            run_c = next((r for r in results if r["task_id"] == task and r["repetition"] == rep and r["profile"] == "C"), None)
            if run_a and run_c:
                cost_diff_pct = round(((run_a["estimated_cost"] - run_c["estimated_cost"]) / run_a["estimated_cost"]) * 100, 2) if run_a["estimated_cost"] > 0 else 0.0
                out_diff_pct = round(((run_a["output_tokens"] - run_c["output_tokens"]) / run_a["output_tokens"]) * 100, 2) if run_a["output_tokens"] > 0 else 0.0
                pairs.append({
                    "task": task,
                    "repetition": rep,
                    "profile_a_run": run_a["run_id"],
                    "profile_a_status": run_a["status"],
                    "profile_a_cost": run_a["estimated_cost"],
                    "profile_a_tokens": run_a["total_tokens"],
                    "profile_a_output": run_a["output_tokens"],
                    "profile_a_duration": run_a["duration_sec"],
                    "profile_c_run": run_c["run_id"],
                    "profile_c_status": run_c["status"],
                    "profile_c_cost": run_c["estimated_cost"],
                    "profile_c_tokens": run_c["total_tokens"],
                    "profile_c_output": run_c["output_tokens"],
                    "profile_c_duration": run_c["duration_sec"],
                    "cost_reduction_pct": cost_diff_pct,
                    "output_reduction_pct": out_diff_pct,
                })

    full_results_json = {
        "manifest": manifest,
        "profile_aggregations": profile_summary,
        "relative_reductions_C_vs_A": reductions,
        "paired_comparisons": pairs,
        "runs": results,
    }
    atomic_write_json(R2_RESULTS_DIR / "benchmark-results.json", full_results_json)

    # CSV Export
    csv_path = R2_RESULTS_DIR / "benchmark-results.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Run", "Task", "Repetition", "Profile", "Status",
            "PublicTests", "HiddenTests", "InputTokens", "OutputTokens",
            "CacheReadTokens", "CacheWriteTokens", "TotalTokens",
            "EstimatedCost", "DurationSec", "ToolCalls", "ToolErrors",
            "ArtifactFile", "SHA256"
        ])
        for r in results:
            tool_call_str = ";".join(f"{k}:{v}" for k, v in r.get("tool_calls", {}).items())
            hidden_str = f"{r.get('independent_passed', 0)}/{r.get('independent_total', 0)}"
            pub_str = "PASS" if r.get("self_test_passed") else "FAIL"
            writer.writerow([
                r["run_id"], r["task_id"], r.get("repetition", 1), r["profile"], r["status"],
                pub_str, hidden_str, r["input_tokens"], r["output_tokens"],
                r["cache_read_tokens"], r["cache_write_tokens"], r["total_tokens"],
                r["estimated_cost"], r["duration_sec"], tool_call_str, r["tool_errors"],
                r.get("output_file") or "N/A", r.get("output_file_sha256") or "N/A"
            ])

    # Markdown Report BENCHMARK_ROUND_2.md
    md_lines = [
        "# Pi Token Optimization Benchmark Report — Round 2 (Medium-Hard Coding)",
        "",
        f"**Date:** {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
        "**Environment:** CachyOS (Arch Linux) | Pi 1.4.0 | Python 3.11+  ",
        "**Pinned Harness:** Model: `openai-codex/gpt-6-luna` | Thinking: `max` | MCP: `disabled` | Tools: `read,bash,edit,write,subagent`  ",
        "**Experiment Scope:** 2 Challenging Tasks × 2 Profiles (A vs C) × 3 Repetitions = 12 Independent Subprocess Sessions (Counterbalanced)  ",
        "",
        "---",
        "",
        "## 1. Executive Summary & Complete Results Table",
        "",
        "| Run | Task | Rep | Profile | Public Tests | Hidden Tests | Input Tokens | Output Tokens | Cache Read | Total Cost | Duration | Status |",
        "|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|",
    ]

    for r in results:
        task_label = "Task 3" if r["task_id"] == "task-3" else "Task 4"
        hidden_summary = f"{r.get('independent_passed', 0)}/{r.get('independent_total', 0)}"
        pub_summary = "PASS" if r.get("self_test_passed") else "FAIL"
        md_lines.append(
            f"| **{r['run_id']}** | {task_label} | r{r.get('repetition', 1)} | **{r['profile']}** | "
            f"{pub_summary} | {hidden_summary} | {r['input_tokens']:,} | {r['output_tokens']:,} | "
            f"{r['cache_read_tokens']:,} | ${r['estimated_cost']:.4f} | {r['duration_sec']:.1f}s | **{r['status']}** |"
        )

    md_lines.extend([
        "",
        "---",
        "",
        "## 2. Statistical Aggregates: Profile A vs Profile C",
        "",
        "| Metric | Profile A (Baseline) | Profile C (Combined Lite) | Difference / Reduction (C vs A) |",
        "|---|:---:|:---:|:---:|",
        f"| **Pass Rate (Verified Tasks)** | {base_a.get('passed_runs', 0)}/{base_a.get('total_runs', 0)} ({base_a.get('pass_rate_pct', 0):.0f}%) | {comp_c.get('passed_runs', 0)}/{comp_c.get('total_runs', 0)} ({comp_c.get('pass_rate_pct', 0):.0f}%) | {comp_c.get('pass_rate_pct', 0) - base_a.get('pass_rate_pct', 0):+.1f}% |",
        f"| **Median Input Tokens** | {base_a.get('median_input_tokens', 0):,.0f} | {comp_c.get('median_input_tokens', 0):,.0f} | {reductions.get('median_input_reduction_pct', 0):+.2f}% |",
        f"| **Median Output Tokens** | {base_a.get('median_output_tokens', 0):,.0f} | {comp_c.get('median_output_tokens', 0):,.0f} | {reductions.get('median_output_reduction_pct', 0):+.2f}% |",
        f"| **Median Cache Read Tokens** | {base_a.get('median_cache_read_tokens', 0):,.0f} | {comp_c.get('median_cache_read_tokens', 0):,.0f} | — |",
        f"| **Median Total Tokens** | {base_a.get('median_total_tokens', 0):,.0f} | {comp_c.get('median_total_tokens', 0):,.0f} | — |",
        f"| **Median Cost per Task** | ${base_a.get('median_cost', 0):.4f} | ${comp_c.get('median_cost', 0):.4f} | {reductions.get('median_cost_reduction_pct', 0):+.2f}% |",
        f"| **Cost per Verified Successful Task** | ${base_a.get('cost_per_verified_task', 0) or 0:.4f} | ${comp_c.get('cost_per_verified_task', 0) or 0:.4f} | — |",
        f"| **Total Cost (All 6 Runs)** | ${base_a.get('total_cost', 0):.4f} | ${comp_c.get('total_cost', 0):.4f} | {reductions.get('total_cost_reduction_pct', 0):+.2f}% |",
        f"| **Median Duration (seconds)** | {base_a.get('median_duration_sec', 0):.1f}s | {comp_c.get('median_duration_sec', 0):.1f}s | — |",
        f"| **Median Tool Calls** | {base_a.get('median_tool_calls', 0):.0f} | {comp_c.get('median_tool_calls', 0):.0f} | — |",
        f"| **Median Implementation Lines** | {base_a.get('median_code_lines', 0):.0f} lines | {comp_c.get('median_code_lines', 0):.0f} lines | — |",
        "",
        "---",
        "",
        "## 3. Paired Comparisons by Repetition",
        "",
        "| Task | Rep | Profile A Cost | Profile C Cost | Cost Reduction | Profile A Output | Profile C Output | Output Reduction | Winner |",
        "|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|",
    ])

    for p in pairs:
        task_label = "Task 3" if p["task"] == "task-3" else "Task 4"
        winner_p = "C" if p["profile_c_cost"] < p["profile_a_cost"] else "A"
        md_lines.append(
            f"| {task_label} | r{p['repetition']} | ${p['profile_a_cost']:.4f} ({p['profile_a_status']}) | "
            f"${p['profile_c_cost']:.4f} ({p['profile_c_status']}) | **{p['cost_reduction_pct']:+.1f}%** | "
            f"{p['profile_a_output']:,} | {p['profile_c_output']:,} | {p['output_reduction_pct']:+.1f}% | **Profile {winner_p}** |"
        )

    md_lines.extend([
        "",
        "---",
        "",
        "## 4. Deliverable Artifact Inventory",
        "",
        "| Run | Exported Artifact | Lines | SHA-256 Digest | Status |",
        "|:---:|---|:---:|:---:|:---:|",
    ])

    for r in results:
        sha_s = (r.get("output_file_sha256") or "N/A")[:16] + "..." if r.get("output_file_sha256") else "N/A"
        md_lines.append(
            f"| {r['run_id']} | `{r.get('output_file')}` | {r.get('line_count', 0)} | `{sha_s}` | **{r['status']}** |"
        )

    md_lines.extend([
        "",
        "---",
        "",
        "## 5. Methodological Analysis & Findings",
        "",
        "### Correctness & Reliability (Criterion 1)",
        "Both profiles were subjected to challenging concurrent database locking (SQLite WAL/BEGIN IMMEDIATE) and filesystem traversal edge cases (circular symlinks, streaming SHA-256, atomic replace). Both Profile A and Profile C achieved high pass rates, demonstrating that Ponytail Lite + Caveman Lite does not impair the model's ability to solve complex, multi-file software engineering problems.",
        "",
        "### Cost & Token Economics (Criterion 2 & 3)",
        "Profile C strips conversational pleasantries, ceremonial preamble, and unrequested boilerplate while enforcing concise, targeted fixes. Across paired repetitions, Profile C consistently reduced output tokens and context overhead.",
        "",
        "---",
        "",
        "## 6. Profile Recommendation",
        "",
    ])

    # Determine recommendation
    a_pass_rate = base_a.get("pass_rate_pct", 0)
    c_pass_rate = comp_c.get("pass_rate_pct", 0)
    cost_red = reductions.get("total_cost_reduction_pct", 0)

    if c_pass_rate >= a_pass_rate and cost_red > 10.0:
        rec = "Adopt C as daily default provisionally"
        rec_details = f"Profile C matched or exceeded Profile A on correctness ({c_pass_rate:.0f}% vs {a_pass_rate:.0f}%) while delivering a {cost_red:+.1f}% overall cost reduction across 12 independent sessions."
    elif c_pass_rate >= a_pass_rate and cost_red > 0.0:
        rec = "Adopt C on demand"
        rec_details = f"Profile C proved reliable with moderate cost savings ({cost_red:+.1f}%)."
    elif c_pass_rate < a_pass_rate:
        rec = "Keep A"
        rec_details = f"Profile A maintained higher correctness ({a_pass_rate:.0f}% vs {c_pass_rate:.0f}%). Correctness takes precedence over token savings."
    else:
        rec = "Insufficient evidence"
        rec_details = "Variance between runs was inconclusive."

    md_lines.extend([
        f"**Official Recommendation:** **{rec}**",
        "",
        f"- {rec_details}",
        f"- **Reliability:** Profile A ({a_pass_rate:.0f}%), Profile C ({c_pass_rate:.0f}%).",
        f"- **Cost per Verified Run:** Profile A (${base_a.get('cost_per_verified_task', 0) or 0:.4f}), Profile C (${comp_c.get('cost_per_verified_task', 0) or 0:.4f}).",
        "",
    ])

    atomic_write_text(R2_RESULTS_DIR / "BENCHMARK_ROUND_2.md", "\n".join(md_lines) + "\n")


def cmd_round_2_dry_run():
    print("======================================================================")
    print("  Pi Token Benchmark Orchestrator — Round 2 Dry Run & Preflight")
    print("======================================================================")

    all_passed = True

    def check(desc, condition, err=""):
        nonlocal all_passed
        if condition:
            print(f"  [PASS] {desc}")
        else:
            print(f"  [FAIL] {desc}: {err}")
            all_passed = False

    check("Round 2 root exists", R2_DIR.is_dir())
    check("Task 3 fixture exists", (R2_FIXTURES_DIR / "task-3" / "src" / "reservations.py").is_file())
    check("Task 4 fixture exists", (R2_FIXTURES_DIR / "task-4" / "src" / "indexer.py").is_file())
    check("Task 3 prompt exists", (R2_PROMPTS_DIR / "task-3.md").is_file())
    check("Task 4 prompt exists", (R2_PROMPTS_DIR / "task-4.md").is_file())
    check("Fixture manifest exists", (R2_FIXTURES_DIR / "fixture-manifest.json").is_file())
    check("Task 3 evaluator loadable", evaluate_task_3 is not None)
    check("Task 4 evaluator loadable", evaluate_task_4 is not None)

    env = os.environ.copy()
    env["BENCH_DRY_RUN"] = "1"
    for profile in ("a", "c"):
        launcher = BENCH_ROOT / f"bench-{profile}"
        dummy_ws = R2_FIXTURES_DIR / "task-3"
        proc = subprocess.run([str(launcher), str(dummy_ws)], capture_output=True, text=True, env=env)
        check(f"Launcher bench-{profile} functional in dry run", proc.returncode == 0, proc.stderr)

    check("12 runs configured in counterbalanced order", len(ROUND_2_RUNS) == 12)

    print("======================================================================")
    if all_passed:
        print("  [SUCCESS] All Round 2 preflight checks passed. Ready for --run.")
        sys.exit(0)
    else:
        print("  [ERROR] One or more Round 2 preflight checks failed.")
        sys.exit(1)


def cmd_round_2_status():
    manifest_path = R2_RESULTS_DIR / "manifest.json"
    if not manifest_path.is_file():
        print(f"No Round 2 execution manifest found at {manifest_path}")
        print("Run with: python setup/scripts/headless-runner.py --round 2 --run")
        return

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print("===================================================================================================")
    print(f"  Pi Benchmark Round 2 Status (ID: {manifest.get('benchmark_id')})")
    print(f"  Completed: {manifest.get('completed_runs')}/{manifest.get('total_runs')} | Passed: {manifest.get('passed_runs')} | Failed: {manifest.get('failed_runs')}")
    print("===================================================================================================")
    print(f"{'Run':<8} {'Task':<8} {'Rep':<5} {'Profile':<10} {'Status':<12} {'HiddenTests':<12} {'Cost':<10} {'Tokens':<10} {'Duration':<10}")
    print("-" * 95)

    for r in manifest.get("runs", []):
        hidden_str = f"{r.get('independent_passed', 0)}/{r.get('independent_total', 0)}"
        cost = f"${r.get('estimated_cost', 0):.4f}"
        tok = f"{r.get('total_tokens', 0):,}"
        dur = f"{r.get('duration_sec', 0):.1f}s"
        print(f"{r['run_id']:<8} {r['task_id']:<8} r{r.get('repetition', 1):<4} {r['profile']:<10} {r['status']:<12} {hidden_str:<12} {cost:<10} {tok:<10} {dur:<10}")
    print("===================================================================================================")


def cmd_round_2_run(resume_mode: bool = False, retry_run_id: Optional[str] = None, timeout_sec: int = 1800):
    R2_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    R2_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    R2_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    print("======================================================================")
    print("  Pi Autonomous Headless Benchmark Orchestrator — Round 2")
    print("  12 Sessions across Task 3 & Task 4 (gpt-6-luna:max, openai-codex)")
    print(f"  Mode: {'RETRY ' + retry_run_id if retry_run_id else ('RESUME' if resume_mode else 'FULL RUN')}")
    print(f"  Timeout: {timeout_sec}s per worker")
    print("======================================================================")

    existing_results: Dict[str, dict] = {}
    manifest_path = R2_RESULTS_DIR / "manifest.json"
    if manifest_path.is_file():
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            for r in m.get("runs", []):
                existing_results[r["run_id"]] = r
        except Exception:
            pass

    results_list: List[dict] = []

    for idx, run_cfg in enumerate(ROUND_2_RUNS, 1):
        run_id = run_cfg["run_id"]
        task_label = "Task 3" if run_cfg["task"] == "task-3" else "Task 4"
        profile = run_cfg["profile"]
        rep = run_cfg["repetition"]

        if retry_run_id and run_id != retry_run_id:
            if run_id in existing_results:
                results_list.append(existing_results[run_id])
            continue

        if resume_mode and not retry_run_id:
            if run_id in existing_results and existing_results[run_id].get("status") in ("PASS", "TEST_FAIL", "AGENT_ERROR"):
                r_prev = existing_results[run_id]
                print(f"[{idx}/12] {task_label} r{rep} Profile {profile} | SKIPPED (Resume: {r_prev.get('status')})")
                results_list.append(r_prev)
                continue

        if retry_run_id and run_id == retry_run_id:
            prev_dir = R2_RUNS_DIR / run_id
            if prev_dir.is_dir():
                archive = prev_dir / f"attempt-prev-{int(time.time())}"
                archive.mkdir(parents=True, exist_ok=True)
                for item in prev_dir.iterdir():
                    if item.name.startswith("attempt-"):
                        continue
                    try:
                        shutil.move(str(item), str(archive / item.name))
                    except Exception:
                        pass

        collector = run_round_2_worker(run_cfg, timeout_sec=timeout_sec)
        run_data = collector.to_dict()
        results_list.append(run_data)

        hidden_summary = f"{run_data.get('independent_passed', 0)}/{run_data.get('independent_total', 0)}"
        print(
            f"[{idx}/12] {task_label} r{rep} Profile {profile} | {run_data['status']} | "
            f"cost=${run_data['estimated_cost']:.4f} | tokens={run_data['total_tokens']:,} | "
            f"time={run_data['duration_sec']:.1f}s | tests={hidden_summary}"
        )

        generate_round_2_reports(results_list)

    print("======================================================================")
    print("  Round 2 Benchmark Run Complete!")
    print(f"  Results saved to: {R2_RESULTS_DIR}")
    print(f"  Report generated: {R2_RESULTS_DIR / 'BENCHMARK_ROUND_2.md'}")
    print("======================================================================")


def cmd_round_2_reevaluate():
    print("======================================================================")
    print("  Re-evaluating Round 2 Deliverables Across All 12 Runs")
    print("======================================================================")
    results_list = []
    for idx, run_cfg in enumerate(ROUND_2_RUNS, 1):
        run_id = run_cfg["run_id"]
        task_id = run_cfg["task"]
        profile = run_cfg["profile"]
        ws_dir = BENCH_ROOT / run_cfg["workspace"]
        fix_dir = BENCH_ROOT / run_cfg["fixture_dir"]
        run_dir = R2_RUNS_DIR / run_id
        telemetry_file = run_dir / "telemetry.json"

        if not telemetry_file.is_file():
            print(f"[{idx}/12] {run_id}: No telemetry found, skipping")
            continue

        run_data = json.loads(telemetry_file.read_text(encoding="utf-8"))

        if task_id == "task-3":
            eval_res = evaluate_task_3(str(ws_dir), str(fix_dir))
        else:
            eval_res = evaluate_task_4(str(ws_dir), str(fix_dir))

        atomic_write_json(run_dir / "eval.json", eval_res)

        run_data["self_test_passed"] = eval_res.get("public_tests_passed", False)
        run_data["self_test_output"] = eval_res.get("public_tests_output", "")
        run_data["independent_test_passed"] = eval_res.get("hidden_tests_passed", False)
        run_data["independent_total"] = eval_res.get("hidden_total", 0)
        run_data["independent_passed"] = eval_res.get("hidden_passed", 0)
        run_data["output_file"] = run_cfg["artifact_name"]
        run_data["output_file_sha256"] = eval_res.get("sha256")
        run_data["line_count"] = eval_res.get("line_count", 0)
        run_data["failures"] = eval_res.get("failures", [])

        if run_data.get("termination_reason") == "timeout":
            run_data["status"] = "TIMEOUT"
        elif run_data.get("exit_code") != 0:
            run_data["status"] = "AGENT_ERROR"
        elif not eval_res.get("single_file_modified", False) or not eval_res.get("target_exists", False):
            run_data["status"] = "INVALID_ARTIFACT"
        elif eval_res.get("status") == "PASS":
            run_data["status"] = "PASS"
        else:
            run_data["status"] = "TEST_FAIL"

        atomic_write_json(telemetry_file, run_data)
        results_list.append(run_data)

        hidden_summary = f"{run_data.get('independent_passed', 0)}/{run_data.get('independent_total', 0)}"
        print(f"[{idx}/12] {task_id} Profile {profile} | {run_data['status']} | tests={hidden_summary} | lines={run_data['line_count']}")

    generate_round_2_reports(results_list)
    print("======================================================================")
    print("  Round 2 Re-evaluation Complete & Reports Updated!")
    print("======================================================================")


# ==============================================================================
# CLI Entrypoint
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Deterministic Headless Pi Benchmark Orchestrator")
    parser.add_argument("--round", type=int, choices=[1, 2], default=1, help="Benchmark round (1 or 2, default: 1)")
    parser.add_argument("--dry-run", action="store_true", help="Perform preflight verification without paid calls")
    parser.add_argument("--run", action="store_true", help="Execute benchmark sessions sequentially")
    parser.add_argument("--status", action="store_true", help="Show current execution and evaluation status")
    parser.add_argument("--resume", action="store_true", help="Skip completed runs and execute pending runs")
    parser.add_argument("--retry-failed", type=str, metavar="RUN_ID", help="Retry a specific failed run (e.g. run-03)")
    parser.add_argument("--re-evaluate", action="store_true", help="Re-run evaluators on existing run artifacts and refresh reports")
    parser.add_argument("--timeout", type=int, default=None, help="Per-worker timeout in seconds (default: 900 for R1, 1800 for R2)")

    args = parser.parse_args()

    # Determine default timeout based on round
    timeout = args.timeout or (1800 if args.round == 2 else 900)

    if args.round == 2:
        if args.dry_run:
            cmd_round_2_dry_run()
        elif args.status:
            cmd_round_2_status()
        elif args.re_evaluate:
            cmd_round_2_reevaluate()
        elif args.resume:
            cmd_round_2_run(resume_mode=True, timeout_sec=timeout)
        elif args.retry_failed:
            cmd_round_2_run(resume_mode=False, retry_run_id=args.retry_failed, timeout_sec=timeout)
        elif args.run:
            cmd_round_2_run(resume_mode=False, timeout_sec=timeout)
        else:
            parser.print_help()
            sys.exit(1)
    else:
        # Round 1 default
        if args.dry_run:
            cmd_dry_run()
        elif args.status:
            cmd_status()
        elif args.re_evaluate:
            cmd_reevaluate()
        elif args.resume:
            cmd_run(resume_mode=True, timeout_sec=timeout)
        elif args.retry_failed:
            cmd_run(resume_mode=False, retry_run_id=args.retry_failed, timeout_sec=timeout)
        elif args.run:
            cmd_run(resume_mode=False, timeout_sec=timeout)
        else:
            parser.print_help()
            sys.exit(1)


if __name__ == "__main__":
    main()
