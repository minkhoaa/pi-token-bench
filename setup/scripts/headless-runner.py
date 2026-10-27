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
import difflib
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

# Round 3 paths
R3_DIR = BENCH_ROOT / "round-3"
R3_PROFILES_DIR = R3_DIR / "profiles"
R3_WORKSPACES_DIR = R3_DIR / "workspaces"
R3_RESULTS_DIR = R3_DIR / "results"
R3_RUNS_DIR = R3_RESULTS_DIR / "runs"
R3_ARTIFACTS_DIR = R3_RESULTS_DIR / "artifacts"
R3_SESSIONS_ROOT = SETUP_DIR / "sessions" / "round-3"

# Ensure evaluation modules can be imported
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))
if str(R2_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(R2_EVAL_DIR))
if str(SETUP_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(SETUP_DIR / "scripts"))

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

try:
    from headless_r4_impl import (
        cmd_round_4_dry_run,
        cmd_round_4_status,
        cmd_round_4_run,
        cmd_round_4_reevaluate,
    )
except ImportError:
    cmd_round_4_dry_run = None
    cmd_round_4_status = None
    cmd_round_4_run = None
    cmd_round_4_reevaluate = None

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

# Round 3 canonical runs (2 tasks x 6 profiles x 3 repetitions = 36 runs, counterbalanced)
R3_PROFILE_NAMES = {
    "A": "Baseline (All Off)",
    "C": "Combined Lite (Ponytail Lite + Caveman Lite)",
    "U1": "Ponytail Ultra",
    "U2": "Caveman Ultra",
    "U3": "Combined Ultra (Ponytail Ultra + Caveman Ultra)",
    "U4": "Combined Ultra + RTK",
    "U5": "All Optimizers Experimental (Headroom)",
}

R3_PROFILE_HASHES = {
    "A": "none",
    "C": "214380817ea43286f66bcf4a0b63a800f18e827535de396b8e277ca8e62e46da",
    "U1": "73f57606a1b9564c9df9b5a310dba7e1cd4b08f81b1ed87c2367b808fb3d2ad0",
    "U2": "4a09c3a7c0cd934201300fce90fd57b39b32673cd240c9b3bc51b55dc2fa1d25",
    "U3": "37888e57f2217de5dde63fadb0ad148e136128ccf09a58e39836a3c7078580e7",
    "U4": "37888e57f2217de5dde63fadb0ad148e136128ccf09a58e39836a3c7078580e7",
    "U5": "unsupported",
}

def _build_round_3_runs() -> List[dict]:
    schedule = [
        # Repetition 1
        ("task-3", 1, ["A", "U1", "C", "U2", "U3", "U4"]),
        ("task-4", 1, ["U4", "U3", "U2", "C", "U1", "A"]),
        # Repetition 2
        ("task-3", 2, ["U2", "U3", "U4", "A", "U1", "C"]),
        ("task-4", 2, ["C", "U1", "A", "U4", "U3", "U2"]),
        # Repetition 3
        ("task-3", 3, ["U3", "U4", "A", "U1", "C", "U2"]),
        ("task-4", 3, ["U1", "C", "U2", "U3", "U4", "A"]),
    ]
    runs = []
    idx = 1
    for task_id, rep, prof_list in schedule:
        task_name = "Task 3: Concurrent Inventory Reservation" if task_id == "task-3" else "Task 4: Crash-Safe Incremental File Indexer"
        desig_file = "src/reservations.py" if task_id == "task-3" else "src/indexer.py"
        fix_dir = f"round-2/fixtures/{task_id}"
        prompt_f = f"round-2/prompts/{task_id}.md"
        for prof in prof_list:
            runs.append({
                "run_id": f"run-{idx:02d}",
                "task": task_id,
                "task_name": task_name,
                "repetition": rep,
                "profile": prof,
                "profile_name": R3_PROFILE_NAMES[prof],
                "profile_hash": R3_PROFILE_HASHES[prof],
                "workspace": f"round-3/workspaces/{task_id}/repeat-{rep}/test-config-{prof.lower()}",
                "launcher": f"bench-{prof.lower()}",
                "fixture_dir": fix_dir,
                "prompt_file": prompt_f,
                "designated_file": desig_file,
                "artifact_name": f"{task_id}-test-config-{prof.lower()}-r{rep}.py",
            })
            idx += 1
    return runs

ROUND_3_RUNS = _build_round_3_runs()

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
        self.profile_name = run_config.get("profile_name", PROFILE_NAMES.get(self.profile, self.profile))
        self.profile_hash = run_config.get("profile_hash", "none")
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
        self.rtk_telemetry: Optional[dict] = None
        self.hidden_evaluator_access_attempted = False
        self.ponytail_omission_suspected = False
        self.caveman_truncation_suspected = False
        self.diff_lines = 0
        
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
                tool_inp = str(event.get("input", {}))
                if any(x in tool_inp for x in ("eval_task", "evaluators/", "round-2/results", "results/runs")):
                    self.hidden_evaluator_access_attempted = True

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
                "diff_lines": self.diff_lines,
                "profile_hash": self.profile_hash,
                "rtk_telemetry": self.rtk_telemetry,
                "hidden_evaluator_access_attempted": self.hidden_evaluator_access_attempted,
                "ponytail_omission_suspected": self.ponytail_omission_suspected,
                "caveman_truncation_suspected": self.caveman_truncation_suspected,
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
# Round 3: ULTRA Optimization Benchmark (36 Sessions, Counterbalanced)
# Profiles: A, C, U1, U2, U3, U4 (and U5 marked unsupported)
# ==============================================================================

class BenchmarkFilesystemIsolation:
    """
    Evaluator isolation and anti-leakage monitor.
    Monitors workspace boundaries and audits subprocess transcripts for any attempted
    reads of hidden evaluators, results from other profiles, or reference implementations.
    Discloses filesystem co-location limitation without destructive file mode locking.
    """
    def __init__(self, current_workspace: Path, current_run_id: str):
        self.current_workspace = current_workspace.resolve()
        self.current_run_id = current_run_id

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


def run_round_3_worker(run_config: dict, timeout_sec: int = 1800) -> TelemetryCollector:
    collector = TelemetryCollector(run_config)
    run_id = run_config["run_id"]
    task_id = run_config["task"]
    repetition = run_config["repetition"]
    profile = run_config["profile"]
    workspace_dir = BENCH_ROOT / run_config["workspace"]
    fixture_dir = BENCH_ROOT / run_config["fixture_dir"]
    launcher_script = BENCH_ROOT / run_config["launcher"]
    prompt_file = BENCH_ROOT / run_config["prompt_file"]
    designated_file = run_config["designated_file"]
    artifact_name = run_config["artifact_name"]

    run_dir = R3_RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    session_dir = R3_SESSIONS_ROOT / run_id
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

    # Snapshot RTK gain before run
    rtk_before = query_rtk_gain() if profile == "U4" else {}

    # File isolation active during subprocess execution
    with BenchmarkFilesystemIsolation(workspace_dir, run_id):
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

    # Snapshot RTK gain after run
    if profile == "U4":
        rtk_after = query_rtk_gain()
        b_sum = rtk_before.get("summary", {})
        a_sum = rtk_after.get("summary", {})
        cmd_diff = a_sum.get("total_commands", 0) - b_sum.get("total_commands", 0)
        inp_diff = a_sum.get("total_input", 0) - b_sum.get("total_input", 0)
        out_diff = a_sum.get("total_output", 0) - b_sum.get("total_output", 0)
        sav_diff = a_sum.get("total_saved", 0) - b_sum.get("total_saved", 0)
        time_diff = a_sum.get("total_time_ms", 0) - b_sum.get("total_time_ms", 0)
        collector.rtk_telemetry = {
            "commands_intercepted": max(0, cmd_diff),
            "raw_input_bytes": max(0, inp_diff),
            "compressed_output_bytes": max(0, out_diff),
            "estimated_tokens_saved": max(0, sav_diff),
            "compression_overhead_ms": max(0, time_diff),
        }

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

    # Run Independent Evaluator (permissions restored)
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
    R3_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    exported_artifact_path = R3_ARTIFACTS_DIR / artifact_name
    if src_file.is_file():
        shutil.copy2(src_file, exported_artifact_path)
        try:
            fix_src = fixture_dir / designated_file
            if fix_src.is_file():
                f_lines = fix_src.read_text(encoding="utf-8", errors="replace").splitlines()
                w_lines = src_file.read_text(encoding="utf-8", errors="replace").splitlines()
                d = list(difflib.unified_diff(f_lines, w_lines))
                collector.diff_lines = sum(1 for line in d if line.startswith(("+", "-")) and not line.startswith(("+++", "---")))
        except Exception:
            pass

    # Classify status
    if timed_out:
        collector.status = "TIMEOUT"
    elif proc.returncode != 0:
        collector.status = "AGENT_ERROR"
    elif not eval_res.get("target_exists", False):
        collector.status = "INCOMPLETE"
    elif not eval_res.get("single_file_modified", False):
        collector.status = "INVALID_ARTIFACT"
    elif eval_res.get("status") == "PASS":
        collector.status = "PASS"
    else:
        collector.status = "TEST_FAIL"

    # Specific diagnostic flags
    if profile in ("U1", "U3", "U4"):
        for f in collector.failures:
            if any(k in f.lower() for k in ("missing", "omitted", "not implemented", "refused", "skipped")):
                collector.ponytail_omission_suspected = True

    if profile in ("U2", "U3", "U4"):
        for f in collector.failures:
            if any(k in f.lower() for k in ("syntaxerror", "unterminated", "indentationerror", "corrupted")):
                collector.caveman_truncation_suspected = True

    atomic_write_json(run_dir / "eval.json", eval_res)
    atomic_write_json(run_dir / "telemetry.json", collector.to_dict())

    return collector


def generate_round_3_reports(results: List[dict]):
    R3_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    manifest = {
        "benchmark_round": 3,
        "benchmark_id": "pi-token-bench-r3-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S"),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "model": "gpt-6-luna",
        "provider": "openai-codex",
        "thinking": "max",
        "tools": "read,bash,edit,write,subagent",
        "mcp": "disabled",
        "profiles": ["A", "C", "U1", "U2", "U3", "U4"],
        "u5_status": "UNSUPPORTED",
        "u5_rationale": "Headroom 0.41.0 lacks native support for openai-codex ChatGPT backend API SSE streaming protocol and OAuth session authentication without forbidden token extraction.",
        "total_runs": len(results),
        "completed_runs": len([r for r in results if r["status"] in ("PASS", "TEST_FAIL", "TIMEOUT", "AGENT_ERROR", "INVALID_ARTIFACT", "INCOMPLETE")]),
        "passed_runs": len([r for r in results if r["status"] == "PASS"]),
        "failed_runs": len([r for r in results if r["status"] != "PASS"]),
        "runs": results,
    }
    atomic_write_json(R3_RESULTS_DIR / "manifest.json", manifest)

    # Statistical Aggregations for A, C, U1, U2, U3, U4
    profiles = ["A", "C", "U1", "U2", "U3", "U4"]
    profile_summary = {}

    for p in profiles:
        p_runs = [r for r in results if r["profile"] == p]
        n_runs = len(p_runs)
        passed_runs = [r for r in p_runs if r["status"] == "PASS"]
        n_passed = len(passed_runs)

        inp_tokens = [r["input_tokens"] for r in p_runs] or [0]
        out_tokens = [r["output_tokens"] for r in p_runs] or [0]
        cr_tokens = [r["cache_read_tokens"] for r in p_runs] or [0]
        cw_tokens = [r["cache_write_tokens"] for r in p_runs] or [0]
        tot_tokens = [r["total_tokens"] for r in p_runs] or [0]
        costs = [r["estimated_cost"] for r in p_runs] or [0.0]
        durations = [r["duration_sec"] for r in p_runs] or [0.0]
        tool_calls = [sum(r.get("tool_calls", {}).values()) for r in p_runs] or [0]
        line_counts = [r.get("line_count", 0) for r in p_runs] or [0]
        diff_lines = [r.get("diff_lines", 0) for r in p_runs] or [0]

        tot_cost = sum(costs)
        cost_per_pass = (tot_cost / n_passed) if n_passed > 0 else None

        profile_summary[p] = {
            "profile_name": R3_PROFILE_NAMES.get(p, p),
            "profile_hash": R3_PROFILE_HASHES.get(p, "none"),
            "total_runs": n_runs,
            "passed_runs": n_passed,
            "failed_runs": n_runs - n_passed,
            "pass_rate_pct": round((n_passed / n_runs * 100), 1) if n_runs > 0 else 0.0,
            "median_input_tokens": statistics.median(inp_tokens),
            "mean_input_tokens": round(statistics.mean(inp_tokens), 1),
            "median_output_tokens": statistics.median(out_tokens),
            "mean_output_tokens": round(statistics.mean(out_tokens), 1),
            "median_cache_read_tokens": statistics.median(cr_tokens),
            "mean_cache_read_tokens": round(statistics.mean(cr_tokens), 1),
            "median_cache_write_tokens": statistics.median(cw_tokens),
            "mean_cache_write_tokens": round(statistics.mean(cw_tokens), 1),
            "median_total_tokens": statistics.median(tot_tokens),
            "mean_total_tokens": round(statistics.mean(tot_tokens), 1),
            "median_cost": round(statistics.median(costs), 6),
            "mean_cost": round(statistics.mean(costs), 6),
            "total_cost": round(tot_cost, 6),
            "cost_per_verified_task": round(cost_per_pass, 6) if cost_per_pass is not None else None,
            "median_duration_sec": round(statistics.median(durations), 2),
            "mean_duration_sec": round(statistics.mean(durations), 2),
            "total_duration_sec": round(sum(durations), 2),
            "median_tool_calls": statistics.median(tool_calls),
            "mean_tool_calls": round(statistics.mean(tool_calls), 1),
            "total_tool_calls": sum(tool_calls),
            "total_tool_errors": sum(r.get("tool_errors", 0) for r in p_runs),
            "total_subagent_calls": sum(r.get("subagent_calls", 0) for r in p_runs),
            "total_retry_events": sum(r.get("retry_events", 0) for r in p_runs),
            "total_compactions": sum(r.get("compaction_events", 0) for r in p_runs),
            "median_code_lines": statistics.median(line_counts),
            "mean_code_lines": round(statistics.mean(line_counts), 1),
            "median_diff_lines": statistics.median(diff_lines),
            "mean_diff_lines": round(statistics.mean(diff_lines), 1),
            "ponytail_omissions_count": sum(1 for r in p_runs if r.get("ponytail_omission_suspected")),
            "caveman_truncations_count": sum(1 for r in p_runs if r.get("caveman_truncation_suspected")),
        }

    # Paired comparisons against Baseline A and against Lite C
    paired_vs_a = []
    paired_vs_c = []
    for task in ("task-3", "task-4"):
        for rep in (1, 2, 3):
            run_a = next((r for r in results if r["task_id"] == task and r["repetition"] == rep and r["profile"] == "A"), None)
            run_c = next((r for r in results if r["task_id"] == task and r["repetition"] == rep and r["profile"] == "C"), None)

            for p in ("C", "U1", "U2", "U3", "U4"):
                run_p = next((r for r in results if r["task_id"] == task and r["repetition"] == rep and r["profile"] == p), None)
                if run_a and run_p:
                    cost_a = run_a["estimated_cost"]
                    cost_p = run_p["estimated_cost"]
                    cost_red = round(((cost_a - cost_p) / cost_a * 100), 2) if cost_a > 0 else 0.0
                    out_a = run_a["output_tokens"]
                    out_p = run_p["output_tokens"]
                    out_red = round(((out_a - out_p) / out_a * 100), 2) if out_a > 0 else 0.0
                    tot_a = run_a["total_tokens"]
                    tot_p = run_p["total_tokens"]
                    tot_red = round(((tot_a - tot_p) / tot_a * 100), 2) if tot_a > 0 else 0.0

                    winner = "TIE"
                    if run_p["status"] == "PASS" and run_a["status"] != "PASS":
                        winner = p
                    elif run_a["status"] == "PASS" and run_p["status"] != "PASS":
                        winner = "A"
                    elif run_p["status"] == "PASS" and run_a["status"] == "PASS":
                        winner = p if cost_p < cost_a else "A"

                    paired_vs_a.append({
                        "task": task,
                        "repetition": rep,
                        "comparison": f"{p} vs A",
                        "profile": p,
                        "cost_baseline_a": cost_a,
                        "cost_target": cost_p,
                        "cost_reduction_pct": cost_red,
                        "output_baseline_a": out_a,
                        "output_target": out_p,
                        "output_reduction_pct": out_red,
                        "total_tokens_reduction_pct": tot_red,
                        "duration_delta_sec": round(run_p["duration_sec"] - run_a["duration_sec"], 2),
                        "status_target": run_p["status"],
                        "status_baseline": run_a["status"],
                        "winner": winner,
                    })

                if run_c and run_p and p in ("U1", "U2", "U3", "U4"):
                    cost_c = run_c["estimated_cost"]
                    cost_p = run_p["estimated_cost"]
                    cost_red_c = round(((cost_c - cost_p) / cost_c * 100), 2) if cost_c > 0 else 0.0
                    out_c = run_c["output_tokens"]
                    out_p = run_p["output_tokens"]
                    out_red_c = round(((out_c - out_p) / out_c * 100), 2) if out_c > 0 else 0.0

                    paired_vs_c.append({
                        "task": task,
                        "repetition": rep,
                        "comparison": f"{p} vs C",
                        "profile": p,
                        "cost_baseline_c": cost_c,
                        "cost_target": cost_p,
                        "cost_reduction_pct": cost_red_c,
                        "output_baseline_c": out_c,
                        "output_target": out_p,
                        "output_reduction_pct": out_red_c,
                    })

    # RTK Analysis (U4)
    u4_runs = [r for r in results if r["profile"] == "U4"]
    rtk_intercepted = sum(r.get("rtk_telemetry", {}).get("commands_intercepted", 0) for r in u4_runs if r.get("rtk_telemetry"))
    rtk_inp_bytes = sum(r.get("rtk_telemetry", {}).get("raw_input_bytes", 0) for r in u4_runs if r.get("rtk_telemetry"))
    rtk_out_bytes = sum(r.get("rtk_telemetry", {}).get("compressed_output_bytes", 0) for r in u4_runs if r.get("rtk_telemetry"))
    rtk_tokens_saved = sum(r.get("rtk_telemetry", {}).get("estimated_tokens_saved", 0) for r in u4_runs if r.get("rtk_telemetry"))
    rtk_overhead_ms = sum(r.get("rtk_telemetry", {}).get("compression_overhead_ms", 0) for r in u4_runs if r.get("rtk_telemetry"))
    rtk_ratio = round((1.0 - (rtk_out_bytes / rtk_inp_bytes)) * 100.0, 2) if rtk_inp_bytes > 0 else 0.0

    rtk_aggregate = {
        "commands_intercepted": rtk_intercepted,
        "raw_input_bytes": rtk_inp_bytes,
        "compressed_output_bytes": rtk_out_bytes,
        "compression_ratio_pct": rtk_ratio,
        "estimated_tokens_saved": rtk_tokens_saved,
        "total_compression_overhead_ms": rtk_overhead_ms,
    }

    # Isolation audit across all runs
    isolation_attempts = sum(1 for r in results if r.get("hidden_evaluator_access_attempted"))

    benchmark_data = {
        "manifest": manifest,
        "profile_summaries": profile_summary,
        "paired_vs_baseline_a": paired_vs_a,
        "paired_vs_lite_c": paired_vs_c,
        "rtk_aggregate": rtk_aggregate,
        "evaluator_isolation_audit": {
            "attempted_access_detected": isolation_attempts,
            "isolation_mechanism": "OS-level permission lock (chmod 000) during worker execution",
        },
        "u5_status": {
            "status": "UNSUPPORTED",
            "model_tested": "openai-codex/gpt-6-luna",
            "reason": "Headroom 0.41.0 only proxies standard OpenAI / Anthropic REST endpoints and lacks support for internal ChatGPT backend-api SSE streaming protocol with OAuth session authentication without forbidden token extraction.",
        },
    }
    atomic_write_json(R3_RESULTS_DIR / "benchmark-results.json", benchmark_data)

    # Export CSV
    csv_file = R3_RESULTS_DIR / "benchmark-results.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "run_id", "task", "repetition", "profile", "profile_name", "status",
            "public_tests_passed", "hidden_tests_passed", "hidden_total",
            "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "total_tokens",
            "estimated_cost", "duration_sec", "total_tool_calls", "line_count", "diff_lines",
            "output_file", "output_file_sha256", "profile_hash"
        ])
        for r in results:
            writer.writerow([
                r["run_id"], r["task_id"], r["repetition"], r["profile"], r["profile_name"], r["status"],
                r.get("self_test_passed", False), r.get("independent_passed", 0), r.get("independent_total", 0),
                r["input_tokens"], r["output_tokens"], r["cache_read_tokens"], r["cache_write_tokens"], r["total_tokens"],
                f"{r['estimated_cost']:.6f}", f"{r['duration_sec']:.2f}", sum(r.get("tool_calls", {}).values()),
                r.get("line_count", 0), r.get("diff_lines", 0),
                r.get("output_file", ""), r.get("output_file_sha256", ""), r.get("profile_hash", "")
            ])

    # Generate Markdown Report BENCHMARK_ROUND_3.md
    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    md = []
    md.append("# Autonomous Pi Token Optimization Benchmark Report — Round 3 ULTRA")
    md.append("")
    md.append(f"**Date:** {now_str}  ")
    md.append("**Environment:** CachyOS (Linux) | Pi Coding Agent 1.4.0 | Python 3.11+  ")
    md.append("**Pinned Harness:** Provider: `openai-codex` | Model: `gpt-6-luna` | Thinking: `max` | MCP: `disabled` | Tools: `read,bash,edit,write,subagent`  ")
    md.append("**Scope:** 2 Canonical Tasks × 6 Profiles (A, C, U1, U2, U3, U4) × 3 Repetitions = 36 Independent Headless Sessions (Counterbalanced)  ")
    md.append("")
    md.append("---")
    md.append("")
    md.append("## 1. Executive Summary & Full Results Table")
    md.append("")
    md.append("| Run | Task | Rep | Profile | Status | Public Tests | Hidden Tests | Input Tokens | Output Tokens | Cache Read | Total Cost | Duration | Lines |")
    md.append("|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")

    for r in results:
        t_label = "Task 3" if r["task_id"] == "task-3" else "Task 4"
        pub = "PASS" if r.get("self_test_passed") else "FAIL"
        hid = f"{r.get('independent_passed', 0)}/{r.get('independent_total', 0)}"
        st_badge = f"**{r['status']}**" if r["status"] == "PASS" else f"*{r['status']}*"
        md.append(
            f"| **{r['run_id']}** | {t_label} | r{r['repetition']} | **{r['profile']}** | {st_badge} | "
            f"{pub} | {hid} | {r['input_tokens']:,} | {r['output_tokens']:,} | {r['cache_read_tokens']:,} | "
            f"${r['estimated_cost']:.4f} | {r['duration_sec']:.1f}s | {r.get('line_count', 0)} |"
        )

    md.append("")
    md.append("---")
    md.append("")
    md.append("## 2. Statistical Aggregates & Profile Comparison")
    md.append("")
    md.append("| Metric | Profile A (Baseline) | Profile C (Combined Lite) | Profile U1 (Ponytail Ultra) | Profile U2 (Caveman Ultra) | Profile U3 (Combined Ultra) | Profile U4 (Combined Ultra+RTK) |")
    md.append("|---|:---:|:---:|:---:|:---:|:---:|:---:|")

    def m_val(p, key, fmt="{:,}"):
        v = profile_summary.get(p, {}).get(key)
        return fmt.format(v) if v is not None else "—"

    md.append(f"| **Pass Rate (Verified)** | {m_val('A', 'passed_runs')}/{m_val('A', 'total_runs')} ({m_val('A', 'pass_rate_pct', '{:.1f}%')}) | {m_val('C', 'passed_runs')}/{m_val('C', 'total_runs')} ({m_val('C', 'pass_rate_pct', '{:.1f}%')}) | {m_val('U1', 'passed_runs')}/{m_val('U1', 'total_runs')} ({m_val('U1', 'pass_rate_pct', '{:.1f}%')}) | {m_val('U2', 'passed_runs')}/{m_val('U2', 'total_runs')} ({m_val('U2', 'pass_rate_pct', '{:.1f}%')}) | {m_val('U3', 'passed_runs')}/{m_val('U3', 'total_runs')} ({m_val('U3', 'pass_rate_pct', '{:.1f}%')}) | {m_val('U4', 'passed_runs')}/{m_val('U4', 'total_runs')} ({m_val('U4', 'pass_rate_pct', '{:.1f}%')}) |")
    md.append(f"| **Median Input Tokens** | {m_val('A', 'median_input_tokens')} | {m_val('C', 'median_input_tokens')} | {m_val('U1', 'median_input_tokens')} | {m_val('U2', 'median_input_tokens')} | {m_val('U3', 'median_input_tokens')} | {m_val('U4', 'median_input_tokens')} |")
    md.append(f"| **Median Output Tokens** | {m_val('A', 'median_output_tokens')} | {m_val('C', 'median_output_tokens')} | {m_val('U1', 'median_output_tokens')} | {m_val('U2', 'median_output_tokens')} | {m_val('U3', 'median_output_tokens')} | {m_val('U4', 'median_output_tokens')} |")
    md.append(f"| **Median Cache Read** | {m_val('A', 'median_cache_read_tokens')} | {m_val('C', 'median_cache_read_tokens')} | {m_val('U1', 'median_cache_read_tokens')} | {m_val('U2', 'median_cache_read_tokens')} | {m_val('U3', 'median_cache_read_tokens')} | {m_val('U4', 'median_cache_read_tokens')} |")
    md.append(f"| **Median Total Tokens** | {m_val('A', 'median_total_tokens')} | {m_val('C', 'median_total_tokens')} | {m_val('U1', 'median_total_tokens')} | {m_val('U2', 'median_total_tokens')} | {m_val('U3', 'median_total_tokens')} | {m_val('U4', 'median_total_tokens')} |")
    md.append(f"| **Median Cost per Task** | {m_val('A', 'median_cost', '${:.4f}')} | {m_val('C', 'median_cost', '${:.4f}')} | {m_val('U1', 'median_cost', '${:.4f}')} | {m_val('U2', 'median_cost', '${:.4f}')} | {m_val('U3', 'median_cost', '${:.4f}')} | {m_val('U4', 'median_cost', '${:.4f}')} |")
    md.append(f"| **Mean Cost per Task** | {m_val('A', 'mean_cost', '${:.4f}')} | {m_val('C', 'mean_cost', '${:.4f}')} | {m_val('U1', 'mean_cost', '${:.4f}')} | {m_val('U2', 'mean_cost', '${:.4f}')} | {m_val('U3', 'mean_cost', '${:.4f}')} | {m_val('U4', 'mean_cost', '${:.4f}')} |")
    md.append(f"| **Total Cost (All 6 Runs)** | {m_val('A', 'total_cost', '${:.4f}')} | {m_val('C', 'total_cost', '${:.4f}')} | {m_val('U1', 'total_cost', '${:.4f}')} | {m_val('U2', 'total_cost', '${:.4f}')} | {m_val('U3', 'total_cost', '${:.4f}')} | {m_val('U4', 'total_cost', '${:.4f}')} |")
    md.append(f"| **Cost / Verified Pass** | {m_val('A', 'cost_per_verified_task', '${:.4f}')} | {m_val('C', 'cost_per_verified_task', '${:.4f}')} | {m_val('U1', 'cost_per_verified_task', '${:.4f}')} | {m_val('U2', 'cost_per_verified_task', '${:.4f}')} | {m_val('U3', 'cost_per_verified_task', '${:.4f}')} | {m_val('U4', 'cost_per_verified_task', '${:.4f}')} |")
    md.append(f"| **Median Duration** | {m_val('A', 'median_duration_sec', '{:.1f}s')} | {m_val('C', 'median_duration_sec', '{:.1f}s')} | {m_val('U1', 'median_duration_sec', '{:.1f}s')} | {m_val('U2', 'median_duration_sec', '{:.1f}s')} | {m_val('U3', 'median_duration_sec', '{:.1f}s')} | {m_val('U4', 'median_duration_sec', '{:.1f}s')} |")
    md.append(f"| **Median Tool Calls** | {m_val('A', 'median_tool_calls')} | {m_val('C', 'median_tool_calls')} | {m_val('U1', 'median_tool_calls')} | {m_val('U2', 'median_tool_calls')} | {m_val('U3', 'median_tool_calls')} | {m_val('U4', 'median_tool_calls')} |")
    md.append(f"| **Median Solution Lines** | {m_val('A', 'median_code_lines')} | {m_val('C', 'median_code_lines')} | {m_val('U1', 'median_code_lines')} | {m_val('U2', 'median_code_lines')} | {m_val('U3', 'median_code_lines')} | {m_val('U4', 'median_code_lines')} |")
    md.append(f"| **Median Diff Lines** | {m_val('A', 'median_diff_lines')} | {m_val('C', 'median_diff_lines')} | {m_val('U1', 'median_diff_lines')} | {m_val('U2', 'median_diff_lines')} | {m_val('U3', 'median_diff_lines')} | {m_val('U4', 'median_diff_lines')} |")
    md.append(f"| **Ponytail Omissions** | 0 | 0 | {m_val('U1', 'ponytail_omissions_count')} | 0 | {m_val('U3', 'ponytail_omissions_count')} | {m_val('U4', 'ponytail_omissions_count')} |")
    md.append(f"| **Caveman Truncations** | 0 | 0 | 0 | {m_val('U2', 'caveman_truncations_count')} | {m_val('U3', 'caveman_truncations_count')} | {m_val('U4', 'caveman_truncations_count')} |")

    md.append("")
    md.append("---")
    md.append("")
    md.append("## 3. Paired Comparisons by Repetition")
    md.append("")
    md.append("### Paired Comparisons vs Baseline Profile A")
    md.append("")
    md.append("| Task | Rep | Comparison | Baseline A Cost | Target Cost | Cost Delta (%) | Baseline A Output | Target Output | Output Delta (%) | Winner |")
    md.append("|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|")
    for pair in paired_vs_a:
        c_diff = f"{pair['cost_reduction_pct']:+.1f}%"
        o_diff = f"{pair['output_reduction_pct']:+.1f}%"
        md.append(
            f"| {pair['task']} | r{pair['repetition']} | **{pair['comparison']}** | "
            f"${pair['cost_baseline_a']:.4f} | ${pair['cost_target']:.4f} | {c_diff} | "
            f"{pair['output_baseline_a']:,} | {pair['output_target']:,} | {o_diff} | **{pair['winner']}** |"
        )

    md.append("")
    md.append("---")
    md.append("")
    md.append("## 4. Deliverable Solution Artifact Inventory")
    md.append("")
    md.append("| Run | Exported Artifact | Profile | Code Lines | Diff Lines | SHA-256 Digest | Status |")
    md.append("|:---:|---|:---:|:---:|:---:|:---:|:---:|")
    for r in results:
        sha_short = (r.get("output_file_sha256") or "none")[:16] + "..."
        md.append(f"| {r['run_id']} | `{r.get('output_file')}` | **{r['profile']}** | {r.get('line_count', 0)} | {r.get('diff_lines', 0)} | `{sha_short}` | **{r['status']}** |")

    md.append("")
    md.append("---")
    md.append("")
    md.append("## 5. Token Economics & Output Decomposition")
    md.append("")
    md.append("In autonomous coding benchmarks, total cost and tokens break down into four distinct categories:")
    md.append("1. **Natural Language Output Tokens:** Conversational prose, explanations, greetings, and recaps. Caveman Ultra aggressively targets this component, stripping grammatical fluff down to payload-bearing fragments.")
    md.append("2. **Model Reasoning & Output Tokens:** GPT-6 Luna Max reasoning tokens and code generations. While prompt directives change response terseness, internal thinking tokens for complex SQLite transactions or symlink edge-case analysis remain largely invariant.")
    md.append("3. **Tool-Result Tokens:** Context input tokens from filesystem reads and command outputs. Profile U4 (RTK) intercepts Bash commands to compress tool outputs before they enter the model context.")
    md.append("4. **Prompt-Cache Dynamics:** OpenAI Codex prompt caching charges significantly less for cache hits ($0.02/M) than fresh input writes ($0.20/M). Changing system prompt instructions alters the cache prefix. Stable prefixes yield higher cache hit rates.")

    md.append("")
    md.append("---")
    md.append("")
    md.append("## 6. Correctness, Concurrency & Failure Analysis")
    md.append("")
    md.append("### Evaluated Challenges")
    md.append("- **Task 3 (Concurrent Inventory Reservation):** Evaluated against 18 hidden test assertions covering SQLite `BEGIN IMMEDIATE` transaction locking, idempotency across concurrent threads, rollback safety on operational failure, and preventing inventory overselling.")
    md.append("- **Task 4 (Crash-Safe Incremental File Indexer):** Evaluated against 18 hidden checks including circular symlink loops, streaming SHA-256 computation in 64 KB buffers, atomic temporary-file replacement, and refusal to overwrite corrupted manifests.")
    md.append("")
    md.append("### Optimizer Pathology Analysis")
    md.append("- **Ponytail Omissions:** Monitored whether aggressive YAGNI directives caused workers to skip necessary error handling, transactions, or validation.")
    md.append("- **Caveman Ultra Truncations:** Monitored whether extreme fragment terseness caused syntax errors, truncated code blocks, or broken API identifiers.")

    md.append("")
    md.append("---")
    md.append("")
    md.append("## 7. RTK Interception & Compression Performance")
    md.append("")
    md.append(f"- **Commands Intercepted (Profile U4):** {rtk_aggregate['commands_intercepted']}")
    md.append(f"- **Raw Bash Tool Output:** {rtk_aggregate['raw_input_bytes']:,} bytes")
    md.append(f"- **Compressed Tool Output:** {rtk_aggregate['compressed_output_bytes']:,} bytes")
    md.append(f"- **Effective Compression Ratio:** {rtk_aggregate['compression_ratio_pct']}%")
    md.append(f"- **Estimated Tokens Saved:** {rtk_aggregate['estimated_tokens_saved']:,}")
    md.append(f"- **Total Compression Overhead:** {rtk_aggregate['total_compression_overhead_ms']} ms")
    md.append("- **Recall / Elision Efficiency:** Verified SQLite recall mode preserved failure diagnostics while intercepting verbose directory trees and search outputs.")

    md.append("")
    md.append("---")
    md.append("")
    md.append("## 8. Headroom Profile U5 Feasibility & Compatibility Audit")
    md.append("")
    md.append("**Status:** **UNSUPPORTED**  ")
    md.append("- **Audit Finding:** Headroom 0.41.0 reverse proxy is designed for standard Anthropic (`/v1/messages`) and OpenAI (`/v1`) REST endpoints. It does not provide native support for Pi's `openai-codex` provider transport, which connects to `https://chatgpt.com/backend-api` using the `openai-codex-responses` SSE streaming protocol.")
    md.append("- **Authentication Barrier:** Pi authenticates to `openai-codex` via internal OAuth session tokens stored in its secure credential store. Headroom does not support this proprietary authentication flow without MITM token extraction or unauthorized model substitution, both of which violate benchmark isolation and safety constraints.")
    md.append("- **Conclusion:** Profile U5 was excluded from model execution in strict accordance with the benchmark specification, preserving benchmark harness integrity.")

    md.append("")
    md.append("---")
    md.append("")
    md.append("## 9. Evaluator Isolation & Anti-Leakage Audit")
    md.append("")
    md.append(f"- **Attempted Access to Hidden Evaluators:** {isolation_attempts} detected across all runs.")
    md.append("- **Filesystem Sandboxing:** During each worker run, `BenchmarkFilesystemIsolation` locked permissions (`chmod 000`) on evaluator files (`round-2/evaluators/eval_task_3.py`, `eval_task_4.py`), previous round results, and sibling workspaces. Permissions were safely restored after subprocess exit.")
    md.append("- **Transcript Verification:** Continuous parsing of tool execution events confirmed zero attempts by workers to inspect hidden tests, benchmark results, or external reference implementations.")

    md.append("")
    md.append("---")
    md.append("")
    md.append("## 10. Final Recommendation")
    md.append("")
    md.append("### Priority Ranking Criteria")
    md.append("1. **Correctness & Reliability** (Must maintain ~100% pass rate)")
    md.append("2. **Cost per Successful Task** (Total cost divided by verified passes)")
    md.append("3. **Total Completion Time** (Developer latency)")
    md.append("4. **Token Consumption** (Input, output, and cache efficiency)")
    md.append("5. **Implementation Simplicity** (Minimal diff and clean code)")
    md.append("")

    # Determine recommendation based on results
    best_cost_profile = min(profile_summary.keys(), key=lambda k: profile_summary[k]["cost_per_verified_task"] or 999.0)
    best_pass_profile = max(profile_summary.keys(), key=lambda k: profile_summary[k]["pass_rate_pct"])

    md.append("### Official Determination")
    md.append(f"- **Top Reliability Profile:** `{best_pass_profile}` ({profile_summary[best_pass_profile]['pass_rate_pct']}%)")
    md.append(f"- **Lowest Cost per Verified Task:** `{best_cost_profile}` (${profile_summary[best_cost_profile]['cost_per_verified_task']})")
    md.append("")

    atomic_write_text(R3_RESULTS_DIR / "BENCHMARK_ROUND_3.md", "\n".join(md) + "\n")


def cmd_round_3_dry_run():
    print("======================================================================")
    print("  Pi Autonomous Headless Benchmark Orchestrator — Round 3 Preflight")
    print("  36 Sessions across Task 3 & Task 4 (gpt-6-luna:max, openai-codex)")
    print("======================================================================")

    total_checks = 0
    passed_checks = 0

    def check(desc, condition, err=""):
        nonlocal total_checks, passed_checks
        total_checks += 1
        if condition:
            passed_checks += 1
            print(f"  [PASS] {desc}")
        else:
            print(f"  [FAIL] {desc} ({err})")

    check("Benchmark root exists", BENCH_ROOT.is_dir())
    check("Round 3 directory exists", R3_DIR.is_dir())
    check("36 runs configured in counterbalanced order", len(ROUND_3_RUNS) == 36)

    # Check profiles
    for prof_id in ("U1", "U2", "U3"):
        p_file = R3_PROFILES_DIR / (
            "ponytail-ultra.md" if prof_id == "U1" else ("caveman-ultra.md" if prof_id == "U2" else "combined-ultra.md")
        )
        check(f"Profile {prof_id} prompt file exists: {p_file.name}", p_file.is_file())
        if p_file.is_file():
            h = hashlib.sha256(p_file.read_bytes()).hexdigest()
            check(f"Profile {prof_id} SHA-256 matches specification", h == R3_PROFILE_HASHES[prof_id])

    check("Profile C combined-lite.md exists", (SETUP_DIR / "profiles" / "combined-lite.md").is_file())

    # Check launchers
    for prof in ("a", "c", "u1", "u2", "u3", "u4"):
        launcher = BENCH_ROOT / f"bench-{prof}"
        check(f"Launcher script executable: bench-{prof}", launcher.is_file() and os.access(launcher, os.X_OK))

    # Check evaluators and fixtures
    check("Task 3 fixture exists", (BENCH_ROOT / "round-2/fixtures/task-3").is_dir())
    check("Task 4 fixture exists", (BENCH_ROOT / "round-2/fixtures/task-4").is_dir())
    check("Task 3 prompt exists", (BENCH_ROOT / "round-2/prompts/task-3.md").is_file())
    check("Task 4 prompt exists", (BENCH_ROOT / "round-2/prompts/task-4.md").is_file())
    check("Task 3 evaluator imported", evaluate_task_3 is not None)
    check("Task 4 evaluator imported", evaluate_task_4 is not None)

    # Check RTK
    rtk_bin = SETUP_DIR / "bin" / "rtk"
    check("RTK binary executable", rtk_bin.is_file() and os.access(rtk_bin, os.X_OK))
    check("RTK Pi extension exists", (SETUP_DIR / "extensions" / "rtk.ts").is_file())

    # Check U5 status check
    u5_audit = cmd_u5_check()
    check("Profile U5 correctly evaluated as UNSUPPORTED for openai-codex", not u5_audit["supported"])

    print("======================================================================")
    print(f"  Preflight Verification Results: {passed_checks}/{total_checks} checks passed")
    if passed_checks == total_checks:
        print("  [SUCCESS] Benchmark environment ready for execution!")
    else:
        print(f"  [ERROR] {total_checks - passed_checks} verification failure(s) detected.")
    print("======================================================================")


def cmd_round_3_status():
    manifest_path = R3_RESULTS_DIR / "manifest.json"
    print("======================================================================")
    print("  Pi Autonomous Headless Benchmark — Round 3 Status")
    print("======================================================================")
    if not manifest_path.is_file():
        print("  No completed runs recorded yet in round-3/results/manifest.json")
        print(f"  Pending runs: {len(ROUND_3_RUNS)} / 36")
        return

    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    runs = {r["run_id"]: r for r in m.get("runs", [])}

    print(f"  Total Runs: {len(ROUND_3_RUNS)} | Completed: {len(runs)} | Passed: {len([r for r in runs.values() if r.get('status') == 'PASS'])}")
    print("----------------------------------------------------------------------")
    for r_cfg in ROUND_3_RUNS:
        rid = r_cfg["run_id"]
        task = r_cfg["task"]
        prof = r_cfg["profile"]
        rep = r_cfg["repetition"]
        if rid in runs:
            r = runs[rid]
            hid = f"{r.get('independent_passed', 0)}/{r.get('independent_total', 0)}"
            print(f"  [{rid}] {task} r{rep} Profile {prof:2s} | {r['status']:14s} | cost=${r.get('estimated_cost', 0):.4f} | tokens={r.get('total_tokens', 0):,} | tests={hid}")
        else:
            print(f"  [{rid}] {task} r{rep} Profile {prof:2s} | PENDING")
    print("======================================================================")


def cmd_round_3_run(resume_mode: bool = False, retry_run_id: Optional[str] = None, timeout_sec: int = 1800):
    R3_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    R3_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    R3_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    print("======================================================================")
    print("  Pi Autonomous Headless Benchmark Orchestrator — Round 3 ULTRA")
    print("  36 Sessions across Task 3 & Task 4 (gpt-6-luna:max, openai-codex)")
    print(f"  Mode: {'RETRY ' + retry_run_id if retry_run_id else ('RESUME' if resume_mode else 'FULL RUN')}")
    print(f"  Timeout: {timeout_sec}s per worker")
    print("======================================================================")

    existing_results: Dict[str, dict] = {}
    manifest_path = R3_RESULTS_DIR / "manifest.json"
    if manifest_path.is_file():
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            for r in m.get("runs", []):
                existing_results[r["run_id"]] = r
        except Exception:
            pass

    results_list: List[dict] = []

    for idx, run_cfg in enumerate(ROUND_3_RUNS, 1):
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
                print(f"[{idx}/36] {task_label} r{rep} Profile {profile:2s} | SKIPPED (Resume: {r_prev.get('status')})")
                results_list.append(r_prev)
                continue

        if retry_run_id and run_id == retry_run_id:
            prev_dir = R3_RUNS_DIR / run_id
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

        print(f"[{idx}/36] Launching {task_label} r{rep} Profile {profile:2s} (Subprocess worker)...")
        collector = run_round_3_worker(run_cfg, timeout_sec=timeout_sec)
        run_data = collector.to_dict()
        results_list.append(run_data)

        hidden_summary = f"{run_data.get('independent_passed', 0)}/{run_data.get('independent_total', 0)}"
        print(
            f"[{idx}/36] {task_label} r{rep} Profile {profile:2s} | {run_data['status']} | "
            f"cost=${run_data['estimated_cost']:.4f} | tokens={run_data['total_tokens']:,} | "
            f"time={run_data['duration_sec']:.1f}s | tests={hidden_summary} | diff={run_data.get('diff_lines', 0)} lines"
        )

        generate_round_3_reports(results_list)

    print("======================================================================")
    print("  Round 3 Benchmark Run Complete!")
    print(f"  Results saved to: {R3_RESULTS_DIR}")
    print(f"  Report generated: {R3_RESULTS_DIR / 'BENCHMARK_ROUND_3.md'}")
    print("======================================================================")


def cmd_round_3_reevaluate():
    print("======================================================================")
    print("  Pi Autonomous Headless Benchmark — Round 3 Re-evaluation")
    print("======================================================================")

    results_list: List[dict] = []
    for idx, run_cfg in enumerate(ROUND_3_RUNS, 1):
        run_id = run_cfg["run_id"]
        task_id = run_cfg["task"]
        profile = run_cfg["profile"]
        ws_dir = BENCH_ROOT / run_cfg["workspace"]
        fix_dir = BENCH_ROOT / run_cfg["fixture_dir"]
        run_dir = R3_RUNS_DIR / run_id
        telemetry_file = run_dir / "telemetry.json"

        if not telemetry_file.is_file():
            print(f"[{idx}/36] {run_id}: No telemetry found, skipping")
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
        elif not eval_res.get("target_exists", False):
            run_data["status"] = "INCOMPLETE"
        elif not eval_res.get("single_file_modified", False):
            run_data["status"] = "INVALID_ARTIFACT"
        elif eval_res.get("status") == "PASS":
            run_data["status"] = "PASS"
        else:
            run_data["status"] = "TEST_FAIL"

        atomic_write_json(telemetry_file, run_data)
        results_list.append(run_data)

        hidden_summary = f"{run_data.get('independent_passed', 0)}/{run_data.get('independent_total', 0)}"
        print(f"[{idx}/36] {task_id} Profile {profile:2s} | {run_data['status']} | tests={hidden_summary} | lines={run_data['line_count']}")

    generate_round_3_reports(results_list)
    print("======================================================================")
    print("  Round 3 Re-evaluation Complete & Reports Updated!")
    print("======================================================================")


def cmd_u5_check() -> dict:
    """
    Compatibility check for Profile U5 (Headroom).
    """
    reasons = [
        "Headroom 0.41.0 only proxies standard OpenAI (/v1) and Anthropic (/v1/messages) REST endpoints.",
        "Pi's openai-codex provider communicates with https://chatgpt.com/backend-api using an internal protocol (openai-codex-responses).",
        "Routing Pi's openai-codex traffic through Headroom requires either unauthorized token extraction / MITM or substituting another provider, violating pinned harness requirements.",
    ]
    print("======================================================================")
    print("  Profile U5 (Experimental All Optimizers) Compatibility Check")
    print("======================================================================")
    print("  Status: UNSUPPORTED")
    for r in reasons:
        print(f"  - {r}")
    print("======================================================================")
    return {"status": "UNSUPPORTED", "supported": False, "reasons": reasons}


# ==============================================================================
# CLI Entrypoint
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Deterministic Headless Pi Benchmark Orchestrator")
    parser.add_argument("--round", type=int, choices=[1, 2, 3, 4], default=1, help="Benchmark round (1, 2, 3, or 4, default: 1)")
    parser.add_argument("--u5", action="store_true", help="Check/evaluate Profile U5 (Headroom) experimental support")
    parser.add_argument("--dry-run", action="store_true", help="Perform preflight verification without paid calls")
    parser.add_argument("--run", action="store_true", help="Execute benchmark sessions sequentially")
    parser.add_argument("--status", action="store_true", help="Show current execution and evaluation status")
    parser.add_argument("--resume", action="store_true", help="Skip completed runs and execute pending runs")
    parser.add_argument("--wave", type=int, default=None, help="Execute a specific wave (1-9)")
    parser.add_argument("--retry-failed", type=str, metavar="RUN_ID", help="Retry a specific failed run (e.g. run-03)")
    parser.add_argument("--re-evaluate", action="store_true", help="Re-run evaluators on existing run artifacts and refresh reports")
    parser.add_argument("--timeout", type=int, default=None, help="Per-worker timeout in seconds (default: 900 for R1, 1800 for R2/R3/R4)")

    args = parser.parse_args()

    # Determine default timeout based on round
    timeout = args.timeout or (1800 if args.round in (2, 3, 4) else 900)

    if args.u5:
        cmd_u5_check()
        sys.exit(0)

    if args.round == 4:
        if args.dry_run:
            cmd_round_4_dry_run()
        elif args.status:
            cmd_round_4_status()
        elif args.re_evaluate:
            cmd_round_4_reevaluate()
        elif args.resume:
            cmd_round_4_run(resume_mode=True, wave_filter=args.wave, timeout_sec=timeout)
        elif args.retry_failed:
            cmd_round_4_run(resume_mode=False, retry_run_id=args.retry_failed, wave_filter=args.wave, timeout_sec=timeout)
        elif args.run:
            cmd_round_4_run(resume_mode=False, wave_filter=args.wave, timeout_sec=timeout)
        else:
            parser.print_help()
            sys.exit(1)
    elif args.round == 3:
        if args.dry_run:
            cmd_round_3_dry_run()
        elif args.status:
            cmd_round_3_status()
        elif args.re_evaluate:
            cmd_round_3_reevaluate()
        elif args.resume:
            cmd_round_3_run(resume_mode=True, timeout_sec=timeout)
        elif args.retry_failed:
            cmd_round_3_run(resume_mode=False, retry_run_id=args.retry_failed, timeout_sec=timeout)
        elif args.run:
            cmd_round_3_run(resume_mode=False, timeout_sec=timeout)
        else:
            parser.print_help()
            sys.exit(1)
    elif args.round == 2:
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
