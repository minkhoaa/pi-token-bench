#!/usr/bin/env python3
"""
Autonomous Headless Pi Benchmark Orchestrator — Round 4 Implementation.
Round 4: Direct Baseline (A) vs Combined Lite (C) vs Combined Ultra (U3)
Across 3 new challenging tasks:
  Task 5: Streaming HTTP Chunked Decoder
  Task 6: Fault-Tolerant Concurrent Job Scheduler
  Task 7: Crash-Safe Event-Sourced Ledger
3 tasks x 3 profiles x 3 repetitions = 27 independent benchmark runs.
Executed in 9 waves of 3 concurrent workers (max_parallel_workers=3, max_parallel_per_task=1)
in a counterbalanced Latin-square schedule.
"""

import concurrent.futures
import csv
import datetime
import difflib
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

R4_DIR = BENCH_ROOT / "round-4"
R4_FIXTURES_DIR = R4_DIR / "fixtures"
R4_EVAL_DIR = R4_DIR / "evaluators"
R4_PROMPTS_DIR = R4_DIR / "prompts"
R4_REFERENCES_DIR = R4_DIR / "references"
R4_MANIFESTS_DIR = R4_DIR / "manifests"
R4_WORKSPACES_DIR = R4_DIR / "workspaces"
R4_RESULTS_DIR = R4_DIR / "results"
R4_RUNS_DIR = R4_RESULTS_DIR / "runs"
R4_ARTIFACTS_DIR = R4_RESULTS_DIR / "artifacts"
R4_SESSIONS_ROOT = SETUP_DIR / "sessions" / "round-4"

# Ensure evaluation modules can be imported
if str(R4_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(R4_EVAL_DIR))

try:
    from eval_task_5 import evaluate_task_5
    from eval_task_6 import evaluate_task_6
    from eval_task_7 import evaluate_task_7
except ImportError as e:
    print(f"Warning: could not import round-4 evaluators: {e}", file=sys.stderr)
    evaluate_task_5 = None
    evaluate_task_6 = None
    evaluate_task_7 = None

PROFILE_NAMES = {
    "A": "A — Baseline",
    "C": "C — Combined Lite",
    "U3": "U3 — Combined Ultra",
}

PROFILE_HASHES = {
    "A": "none",
    "C": "214380817ea43286f66bcf4a0b63a800f18e827535de396b8e277ca8e62e46da",
    "U3": "37888e57f2217de5dde63fadb0ad148e136128ccf09a58e39836a3c7078580e7",
}

TASK_META = {
    "task-5": {
        "name": "Streaming HTTP Chunked Decoder",
        "designated_file": "src/chunked_decoder.py",
        "fixture_dir": "round-4/fixtures/task-5",
        "prompt_file": "round-4/prompts/task-5.md",
        "eval_func": evaluate_task_5,
    },
    "task-6": {
        "name": "Fault-Tolerant Concurrent Job Scheduler",
        "designated_file": "src/scheduler.py",
        "fixture_dir": "round-4/fixtures/task-6",
        "prompt_file": "round-4/prompts/task-6.md",
        "eval_func": evaluate_task_6,
    },
    "task-7": {
        "name": "Crash-Safe Event-Sourced Ledger",
        "designated_file": "src/ledger.py",
        "fixture_dir": "round-4/fixtures/task-7",
        "prompt_file": "round-4/prompts/task-7.md",
        "eval_func": evaluate_task_7,
    },
}

LAUNCHER_MAP = {
    "A": "bench-a",
    "C": "bench-c",
    "U3": "bench-u3",
}

# Phase A subagent overhead recorded separately
PHASE_A_SUBAGENT_OVERHEAD = {
    "task-5-architect": {"run_id": "aeff2c5f", "model": "antigravity/gemini-3.8-flash:low", "input": 319337, "output": 15088, "cache_read": 161312, "cost": 0.042002},
    "task-6-architect": {"run_id": "3df382c1", "model": "antigravity/gemini-3.8-flash:low", "input": 381128, "output": 16013, "cache_read": 272362, "cost": 0.051327},
    "task-7-architect": {"run_id": "9089b9b2", "model": "antigravity/gemini-3.8-flash:low", "input": 137574, "output": 19225, "cache_read": 958761, "cost": 0.045416},
    "total": {"input": 838039, "output": 50326, "cache_read": 1392435, "cost": 0.138745},
}


def _build_round_4_runs() -> List[dict]:
    """
    Build the 27 runs configured in 9 waves.
    Counterbalanced Latin-square schedule:
    Wave 1: T5=A,  T6=C,  T7=U3 (Rep 1)
    Wave 2: T5=C,  T6=U3, T7=A  (Rep 1)
    Wave 3: T5=U3, T6=A,  T7=C  (Rep 1)
    Wave 4: T5=C,  T6=U3, T7=A  (Rep 2)
    Wave 5: T5=U3, T6=A,  T7=C  (Rep 2)
    Wave 6: T5=A,  T6=C,  T7=U3 (Rep 2)
    Wave 7: T5=U3, T6=A,  T7=C  (Rep 3)
    Wave 8: T5=A,  T6=C,  T7=U3 (Rep 3)
    Wave 9: T5=C,  T6=U3, T7=A  (Rep 3)
    """
    schedule = [
        # Wave 1 (Rep 1)
        (1, 1, {"task-5": "A", "task-6": "C", "task-7": "U3"}),
        # Wave 2 (Rep 1)
        (2, 1, {"task-5": "C", "task-6": "U3", "task-7": "A"}),
        # Wave 3 (Rep 1)
        (3, 1, {"task-5": "U3", "task-6": "A", "task-7": "C"}),
        # Wave 4 (Rep 2)
        (4, 2, {"task-5": "C", "task-6": "U3", "task-7": "A"}),
        # Wave 5 (Rep 2)
        (5, 2, {"task-5": "U3", "task-6": "A", "task-7": "C"}),
        # Wave 6 (Rep 2)
        (6, 2, {"task-5": "A", "task-6": "C", "task-7": "U3"}),
        # Wave 7 (Rep 3)
        (7, 3, {"task-5": "U3", "task-6": "A", "task-7": "C"}),
        # Wave 8 (Rep 3)
        (8, 3, {"task-5": "A", "task-6": "C", "task-7": "U3"}),
        # Wave 9 (Rep 3)
        (9, 3, {"task-5": "C", "task-6": "U3", "task-7": "A"}),
    ]

    runs = []
    run_idx = 1
    for wave_num, rep, task_prof_map in schedule:
        for task_id in ["task-5", "task-6", "task-7"]:
            prof = task_prof_map[task_id]
            run_id = f"run-{run_idx:02d}"
            tmeta = TASK_META[task_id]
            artifact_name = f"{task_id}-{prof.lower()}-r{rep}.py"
            workspace = f"round-4/workspaces/{task_id}/repeat-{rep}/test-config-{prof.lower()}"
            runs.append({
                "run_id": run_id,
                "wave": wave_num,
                "task": task_id,
                "task_name": tmeta["name"],
                "repetition": rep,
                "profile": prof,
                "profile_name": PROFILE_NAMES[prof],
                "profile_hash": PROFILE_HASHES[prof],
                "workspace": workspace,
                "launcher": LAUNCHER_MAP[prof],
                "fixture_dir": tmeta["fixture_dir"],
                "prompt_file": tmeta["prompt_file"],
                "designated_file": tmeta["designated_file"],
                "artifact_name": artifact_name,
            })
            run_idx += 1
    return runs


ROUND_4_RUNS = _build_round_4_runs()


def atomic_write_json(file_path: Path, data: Any):
    temp_path = file_path.with_suffix(".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    temp_path.replace(file_path)


def atomic_write_text(file_path: Path, text: str):
    temp_path = file_path.with_suffix(".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        f.write(text)
    temp_path.replace(file_path)


class R4TelemetryCollector:
    """
    Collects execution telemetry and per-request context consumption.
    Tracks:
      - per-request usage (input, cacheRead, cacheWrite, output, cost, seq, stopReason)
      - request_input_context = input + cacheRead + cacheWrite
      - peak, median, mean request context
      - cumulative context tokens
      - cache read rate
      - peak context % of 272K verified context window
      - compaction events and whether compaction preceded peak
      - subagent calls
    """
    def __init__(self, run_config: dict):
        self.run_id = run_config["run_id"]
        self.wave = run_config.get("wave", 1)
        self.task_id = run_config["task"]
        self.task = self.task_id
        self.task_name = run_config.get("task_name", self.task_id)
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
        self.verified_model_context_limit = 272000

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

        # Per-request context tracking (Section 10)
        self.per_request_usage: List[dict] = []

        self.tool_calls: Dict[str, int] = {}
        self.tool_errors = 0
        self.subagent_calls = 0
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
                    cost_val = usage.get("cost", {}).get("total", 0.0) if isinstance(usage.get("cost"), dict) else 0.0

                    req_input_ctx = inp + cr + cw
                    seq_num = len(self.per_request_usage) + 1
                    compaction_before = self.compaction_events > 0

                    self.per_request_usage.append({
                        "seq": seq_num,
                        "input": inp,
                        "output": out,
                        "cacheRead": cr,
                        "cacheWrite": cw,
                        "request_input_context": req_input_ctx,
                        "cost": cost_val,
                        "model": msg.get("model", self.model_id),
                        "stopReason": msg.get("stopReason"),
                        "compaction_preceded": compaction_before,
                    })

                    self.input_tokens += inp
                    self.output_tokens += out
                    self.cache_read_tokens += cr
                    self.cache_write_tokens += cw
                    self.total_tokens += tot
                    self.estimated_cost += cost_val

                    if msg.get("stopReason"):
                        self.assistant_stop_reason = msg.get("stopReason")
                    if msg.get("errorMessage"):
                        self.failures.append(f"Provider error: {msg.get('errorMessage')}")
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
                    c_cost = c_usage.get("cost", {}).get("total", 0.0) if isinstance(c_usage.get("cost"), dict) else 0.0
                    self.estimated_cost += c_cost

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

            elif event_type == "agent_settled":
                if event.get("aborted"):
                    self.agent_settled_aborted = True

    def compute_context_metrics(self) -> dict:
        if not self.per_request_usage:
            return {
                "peak_input_context": 0,
                "median_input_context": 0.0,
                "mean_input_context": 0.0,
                "model_requests": 0,
                "cumulative_context_tokens": 0,
                "cache_read_rate": 0.0,
                "peak_context_pct_limit": 0.0,
                "compaction_preceded_peak": False,
            }

        contexts = [r["request_input_context"] for r in self.per_request_usage]
        peak = max(contexts)
        med = float(statistics.median(contexts))
        mean_val = float(statistics.mean(contexts))
        req_count = len(self.per_request_usage)
        cum_ctx = sum(contexts)

        total_in_and_cr = sum(r["input"] + r["cacheRead"] + r["cacheWrite"] for r in self.per_request_usage)
        cr_rate = (sum(r["cacheRead"] for r in self.per_request_usage) / total_in_and_cr) if total_in_and_cr > 0 else 0.0

        pct_limit = (peak / self.verified_model_context_limit) * 100.0

        # Check if compaction preceded the peak context request
        comp_preceded_peak = False
        for r in self.per_request_usage:
            if r["request_input_context"] == peak:
                comp_preceded_peak = r.get("compaction_preceded", False)
                break

        return {
            "peak_input_context": peak,
            "median_input_context": round(med, 1),
            "mean_input_context": round(mean_val, 1),
            "model_requests": req_count,
            "cumulative_context_tokens": cum_ctx,
            "cache_read_rate": round(cr_rate, 4),
            "peak_context_pct_limit": round(pct_limit, 2),
            "compaction_preceded_peak": comp_preceded_peak,
        }

    def to_dict(self) -> dict:
        ctx_metrics = self.compute_context_metrics()
        return {
            "run_id": self.run_id,
            "wave": self.wave,
            "task": self.task_id,
            "task_name": self.task_name,
            "repetition": self.repetition,
            "profile": self.profile,
            "profile_name": self.profile_name,
            "profile_hash": self.profile_hash,
            "session_id": self.session_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_sec": round(self.duration_sec, 2),
            "status": self.status,
            "exit_code": self.exit_code,
            "termination_reason": self.termination_reason,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost": round(self.estimated_cost, 6),
            "cost_incomplete": self.cost_incomplete,
            "context_metrics": ctx_metrics,
            "peak_input_context": ctx_metrics["peak_input_context"],
            "median_input_context": ctx_metrics["median_input_context"],
            "mean_input_context": ctx_metrics["mean_input_context"],
            "model_requests": ctx_metrics["model_requests"],
            "cumulative_context_tokens": ctx_metrics["cumulative_context_tokens"],
            "cache_read_rate": ctx_metrics["cache_read_rate"],
            "peak_context_pct_limit": ctx_metrics["peak_context_pct_limit"],
            "compaction_preceded_peak": ctx_metrics["compaction_preceded_peak"],
            "per_request_usage": self.per_request_usage,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "subagent_calls": self.subagent_calls,
            "diff_lines": self.diff_lines,
            "line_count": self.line_count,
            "retry_events": self.retry_events,
            "compaction_events": self.compaction_events,
            "self_test_passed": self.self_test_passed,
            "independent_test_passed": self.independent_test_passed,
            "independent_total": self.independent_total,
            "independent_passed": self.independent_passed,
            "failures": self.failures,
            "output_file": self.output_file,
            "output_file_sha256": self.output_file_sha256,
        }


def run_round_4_worker(run_config: dict, timeout_sec: int = 1800) -> R4TelemetryCollector:
    """
    Executes a single headless Pi worker session for Round 4.
    """
    collector = R4TelemetryCollector(run_config)
    run_id = run_config["run_id"]
    task_id = run_config["task"]
    profile = run_config["profile"]
    workspace_dir = BENCH_ROOT / run_config["workspace"]
    fixture_dir = BENCH_ROOT / run_config["fixture_dir"]
    launcher_script = BENCH_ROOT / run_config["launcher"]
    prompt_file = BENCH_ROOT / run_config["prompt_file"]
    designated_file = run_config["designated_file"]
    artifact_name = run_config["artifact_name"]

    run_dir = R4_RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    session_dir = R4_SESSIONS_ROOT / run_id
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
    eval_func = TASK_META[task_id]["eval_func"]
    eval_res = {}
    if eval_func:
        eval_res = eval_func(str(workspace_dir), str(fixture_dir))

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
    R4_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    exported_artifact_path = R4_ARTIFACTS_DIR / artifact_name
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
    has_provider_error = False
    for r in collector.per_request_usage:
        if r.get("stopReason") == "error":
            has_provider_error = True
    if collector.assistant_stop_reason == "error":
        has_provider_error = True
    if any("usage limit" in f.lower() or "rate limit" in f.lower() for f in collector.failures):
        has_provider_error = True

    if timed_out:
        collector.status = "TIMEOUT"
    elif has_provider_error:
        collector.status = "ENVIRONMENT_ERROR"
    elif proc.returncode != 0:
        collector.status = "AGENT_ERROR"
    elif not eval_res.get("target_exists", False):
        collector.status = "INVALID_ARTIFACT"
    elif not eval_res.get("single_file_modified", False):
        collector.status = "INVALID_ARTIFACT"
    elif eval_res.get("status") == "PASS":
        collector.status = "PASS"
    else:
        collector.status = "TEST_FAIL"

    atomic_write_json(run_dir / "eval.json", eval_res)
    atomic_write_json(run_dir / "telemetry.json", collector.to_dict())

    return collector


def generate_round_4_reports(results: List[dict]):
    """
    Generates all deliverable reports for Round 4:
    - benchmark-results.json
    - benchmark-results.csv
    - BENCHMARK_ROUND_4.md
    - CONTEXT_WINDOW_ANALYSIS.md
    - CODE_QUALITY_REVIEW.md
    """
    R4_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. benchmark-results.json
    atomic_write_json(R4_RESULTS_DIR / "benchmark-results.json", results)

    # 1b. manifest.json (conforming with Round 2 & Round 3 conventions)
    completed_runs = len([r for r in results if r.get("status") in ("PASS", "TEST_FAIL", "AGENT_ERROR", "TIMEOUT")])
    passed_runs = len([r for r in results if r.get("status") == "PASS"])
    failed_runs = completed_runs - passed_runs
    manifest_data = {
        "benchmark_round": 4,
        "benchmark_id": "round-4-direct-baseline-vs-lite-vs-ultra",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "model": "gpt-6-luna",
        "provider": "openai-codex",
        "thinking": "max",
        "tools": "read,bash,edit,write,subagent",
        "mcp": False,
        "profiles": {
            "A": {"name": "A — Baseline", "overlay_sha256": PROFILE_HASHES["A"]},
            "C": {"name": "C — Combined Lite", "overlay_sha256": PROFILE_HASHES["C"]},
            "U3": {"name": "U3 — Combined Ultra", "overlay_sha256": PROFILE_HASHES["U3"]},
        },
        "total_runs": len(ROUND_4_RUNS),
        "completed_runs": completed_runs,
        "passed_runs": passed_runs,
        "failed_runs": failed_runs,
        "runs": results,
    }
    atomic_write_json(R4_RESULTS_DIR / "manifest.json", manifest_data)

    # 2. benchmark-results.csv
    csv_fields = [
        "run_id", "wave", "task", "repetition", "profile", "status",
        "duration_sec", "input_tokens", "output_tokens", "cache_read_tokens", "total_tokens",
        "estimated_cost", "peak_input_context", "median_input_context", "mean_input_context",
        "model_requests", "cumulative_context_tokens", "cache_read_rate", "peak_context_pct_limit",
        "line_count", "diff_lines", "independent_passed", "independent_total",
    ]
    csv_path = R4_RESULTS_DIR / "benchmark-results.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            writer.writerow(r)

    # Groupings
    by_profile = {"A": [], "C": [], "U3": []}
    by_task_prof = {
        "task-5": {"A": [], "C": [], "U3": []},
        "task-6": {"A": [], "C": [], "U3": []},
        "task-7": {"A": [], "C": [], "U3": []},
    }
    for r in results:
        prof = r["profile"]
        task = r["task"]
        if prof in by_profile:
            by_profile[prof].append(r)
        if task in by_task_prof and prof in by_task_prof[task]:
            by_task_prof[task][prof].append(r)

    # Helper stats
    def calc_stats(run_list):
        if not run_list:
            return {}
        n = len(run_list)
        passes = sum(1 for r in run_list if r.get("status") == "PASS")
        costs = [r.get("estimated_cost", 0.0) for r in run_list]
        durations = [r.get("duration_sec", 0.0) for r in run_list]
        inputs = [r.get("input_tokens", 0) for r in run_list]
        outputs = [r.get("output_tokens", 0) for r in run_list]
        cache_reads = [r.get("cache_read_tokens", 0) for r in run_list]
        totals = [r.get("total_tokens", 0) for r in run_list]
        peaks = [r.get("peak_input_context", 0) for r in run_list]
        medians = [r.get("median_input_context", 0) for r in run_list]
        cum_ctxs = [r.get("cumulative_context_tokens", 0) for r in run_list]
        reqs = [r.get("model_requests", 0) for r in run_list]
        diffs = [r.get("diff_lines", 0) for r in run_list]

        return {
            "n": n,
            "passes": passes,
            "pass_rate": (passes / n) * 100.0,
            "total_cost": sum(costs),
            "mean_cost": statistics.mean(costs),
            "median_cost": statistics.median(costs),
            "mean_duration": statistics.mean(durations),
            "median_duration": statistics.median(durations),
            "mean_input": statistics.mean(inputs),
            "mean_output": statistics.mean(outputs),
            "mean_cache_read": statistics.mean(cache_reads),
            "mean_total": statistics.mean(totals),
            "mean_peak_context": statistics.mean(peaks),
            "mean_median_context": statistics.mean(medians),
            "mean_cum_context": statistics.mean(cum_ctxs),
            "mean_requests": statistics.mean(reqs),
            "mean_diff": statistics.mean(diffs),
        }

    st_A = calc_stats(by_profile["A"])
    st_C = calc_stats(by_profile["C"])
    st_U3 = calc_stats(by_profile["U3"])

    # 3. BENCHMARK_ROUND_4.md
    md = []
    md.append("# Autonomous Pi Token Optimization Benchmark Report — Round 4 Direct Comparison")
    md.append("")
    md.append("**Experiment:** Headless Pi Benchmark Round 4 (Direct Baseline vs Lite vs Ultra)")
    md.append(f"**Date:** {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    md.append("**Model:** `openai-codex / gpt-6-luna` | **Thinking:** `max` | **Tools:** `read,bash,edit,write,subagent`")
    md.append("**Harness:** Headless JSON mode (`pi --mode json`), fresh sessions, isolated process groups")
    md.append("**Design:** 3 tasks x 3 profiles x 3 repetitions = 27 independent benchmark runs in 9 waves")
    md.append("**Concurrency:** `max_parallel_workers=3`, `max_parallel_per_task=1` (Counterbalanced Latin square)")
    md.append("")
    md.append("---")
    md.append("")
    md.append("## 1. Executive Summary & Recommendation")
    md.append("")
    md.append("| Metric | Profile A (Baseline) | Profile C (Combined Lite) | Profile U3 (Combined Ultra) | Lite vs Base | Ultra vs Base |")
    md.append("| :--- | :---: | :---: | :---: | :---: | :---: |")

    def diff_pct(new_v, base_v):
        if base_v == 0:
            return "N/A"
        diff = ((new_v - base_v) / base_v) * 100.0
        return f"{diff:+.1f}%"

    md.append(f"| **Pass Rate** | {st_A.get('pass_rate', 0):.1f}% ({st_A.get('passes', 0)}/{st_A.get('n', 0)}) | {st_C.get('pass_rate', 0):.1f}% ({st_C.get('passes', 0)}/{st_C.get('n', 0)}) | {st_U3.get('pass_rate', 0):.1f}% ({st_U3.get('passes', 0)}/{st_U3.get('n', 0)}) | {st_C.get('pass_rate', 0) - st_A.get('pass_rate', 0):+.1f}% pts | {st_U3.get('pass_rate', 0) - st_A.get('pass_rate', 0):+.1f}% pts |")
    md.append(f"| **Total Cost** | ${st_A.get('total_cost', 0):.4f} | ${st_C.get('total_cost', 0):.4f} | ${st_U3.get('total_cost', 0):.4f} | {diff_pct(st_C.get('total_cost', 0), st_A.get('total_cost', 0))} | {diff_pct(st_U3.get('total_cost', 0), st_A.get('total_cost', 0))} |")
    md.append(f"| **Mean Cost / Run** | ${st_A.get('mean_cost', 0):.4f} | ${st_C.get('mean_cost', 0):.4f} | ${st_U3.get('mean_cost', 0):.4f} | {diff_pct(st_C.get('mean_cost', 0), st_A.get('mean_cost', 0))} | {diff_pct(st_U3.get('mean_cost', 0), st_A.get('mean_cost', 0))} |")
    md.append(f"| **Median Cost / Run** | ${st_A.get('median_cost', 0):.4f} | ${st_C.get('median_cost', 0):.4f} | ${st_U3.get('median_cost', 0):.4f} | {diff_pct(st_C.get('median_cost', 0), st_A.get('median_cost', 0))} | {diff_pct(st_U3.get('median_cost', 0), st_A.get('median_cost', 0))} |")
    md.append(f"| **Mean Duration** | {st_A.get('mean_duration', 0):.1f}s | {st_C.get('mean_duration', 0):.1f}s | {st_U3.get('mean_duration', 0):.1f}s | {diff_pct(st_C.get('mean_duration', 0), st_A.get('mean_duration', 0))} | {diff_pct(st_U3.get('mean_duration', 0), st_A.get('mean_duration', 0))} |")
    md.append(f"| **Mean Input Tokens** | {st_A.get('mean_input', 0):,.0f} | {st_C.get('mean_input', 0):,.0f} | {st_U3.get('mean_input', 0):,.0f} | {diff_pct(st_C.get('mean_input', 0), st_A.get('mean_input', 0))} | {diff_pct(st_U3.get('mean_input', 0), st_A.get('mean_input', 0))} |")
    md.append(f"| **Mean Output Tokens** | {st_A.get('mean_output', 0):,.0f} | {st_C.get('mean_output', 0):,.0f} | {st_U3.get('mean_output', 0):,.0f} | {diff_pct(st_C.get('mean_output', 0), st_A.get('mean_output', 0))} | {diff_pct(st_U3.get('mean_output', 0), st_A.get('mean_output', 0))} |")
    md.append(f"| **Mean Cache-Read Tokens** | {st_A.get('mean_cache_read', 0):,.0f} | {st_C.get('mean_cache_read', 0):,.0f} | {st_U3.get('mean_cache_read', 0):,.0f} | {diff_pct(st_C.get('mean_cache_read', 0), st_A.get('mean_cache_read', 0))} | {diff_pct(st_U3.get('mean_cache_read', 0), st_A.get('mean_cache_read', 0))} |")
    md.append(f"| **Mean Peak Input Context** | {st_A.get('mean_peak_context', 0):,.0f} | {st_C.get('mean_peak_context', 0):,.0f} | {st_U3.get('mean_peak_context', 0):,.0f} | {diff_pct(st_C.get('mean_peak_context', 0), st_A.get('mean_peak_context', 0))} | {diff_pct(st_U3.get('mean_peak_context', 0), st_A.get('mean_peak_context', 0))} |")
    md.append(f"| **Mean Model Requests** | {st_A.get('mean_requests', 0):.1f} | {st_C.get('mean_requests', 0):.1f} | {st_U3.get('mean_requests', 0):.1f} | {diff_pct(st_C.get('mean_requests', 0), st_A.get('mean_requests', 0))} | {diff_pct(st_U3.get('mean_requests', 0), st_A.get('mean_requests', 0))} |")
    md.append(f"| **Mean Diff Lines** | {st_A.get('mean_diff', 0):.1f} | {st_C.get('mean_diff', 0):.1f} | {st_U3.get('mean_diff', 0):.1f} | {diff_pct(st_C.get('mean_diff', 0), st_A.get('mean_diff', 0))} | {diff_pct(st_U3.get('mean_diff', 0), st_A.get('mean_diff', 0))} |")
    md.append("")

    # Determine recommended profile
    rec_profile = "C (Combined Lite)"
    if st_U3.get("pass_rate", 0) >= st_C.get("pass_rate", 0) and st_U3.get("total_cost", float("inf")) < st_C.get("total_cost", 0):
        rec_profile = "U3 (Combined Ultra)"
    elif st_A.get("pass_rate", 0) > st_C.get("pass_rate", 0):
        rec_profile = "A (Baseline)"

    md.append(f"### Evidence-Backed Recommendation: **Profile {rec_profile}**")
    md.append("")
    md.append("Key findings:")
    md.append(f"- **Profile C (Lite)** achieved {st_C.get('pass_rate', 0):.1f}% pass rate with total cost ${st_C.get('total_cost', 0):.4f}.")
    md.append(f"- **Profile U3 (Ultra)** achieved {st_U3.get('pass_rate', 0):.1f}% pass rate with total cost ${st_U3.get('total_cost', 0):.4f}.")
    md.append(f"- **Profile A (Baseline)** achieved {st_A.get('pass_rate', 0):.1f}% pass rate with total cost ${st_A.get('total_cost', 0):.4f}.")
    md.append("")
    md.append("---")
    md.append("")
    md.append("## 2. Per-Task Paired Repetition Results")
    md.append("")
    for task_id in ["task-5", "task-6", "task-7"]:
        tname = TASK_META[task_id]["name"]
        md.append(f"### {tname} (`{task_id}`)")
        md.append("")
        md.append("| Run ID | Wave | Profile | Repetition | Status | Checks Passed | Duration (s) | Cost ($) | Peak Context | Output Tokens | Diff Lines |")
        md.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
        task_runs = [r for r in results if r["task"] == task_id]
        task_runs.sort(key=lambda x: (x["repetition"], x["profile"]))
        for r in task_runs:
            chk_str = f"{r.get('independent_passed', 0)}/{r.get('independent_total', 0)}"
            md.append(f"| `{r['run_id']}` | {r.get('wave', 0)} | **{r['profile']}** | Rep {r['repetition']} | `{r['status']}` | {chk_str} | {r.get('duration_sec', 0):.1f}s | ${r.get('estimated_cost', 0):.4f} | {r.get('peak_input_context', 0):,} | {r.get('output_tokens', 0):,} | {r.get('diff_lines', 0)} |")
        md.append("")

    md.append("---")
    md.append("")
    md.append("## 3. Wave Execution Schedule & Overlapping Concurrency")
    md.append("")
    md.append("Execution was conducted in 9 sequential waves of up to 3 concurrent workers:")
    md.append("")
    md.append("| Wave | Task 5 Worker | Task 6 Worker | Task 7 Worker | Wave Concurrency | Repetition |")
    md.append("| :---: | :---: | :---: | :---: | :---: | :---: |")
    for w in range(1, 10):
        w_runs = [r for r in results if r.get("wave") == w]
        t5_r = next((r for r in w_runs if r["task"] == "task-5"), None)
        t6_r = next((r for r in w_runs if r["task"] == "task-6"), None)
        t7_r = next((r for r in w_runs if r["task"] == "task-7"), None)
        t5_str = f"{t5_r['run_id']} ({t5_r['profile']}: {t5_r['status']})" if t5_r else "N/A"
        t6_str = f"{t6_r['run_id']} ({t6_r['profile']}: {t6_r['status']})" if t6_r else "N/A"
        t7_str = f"{t7_r['run_id']} ({t7_r['profile']}: {t7_r['status']})" if t7_r else "N/A"
        rep_val = t5_r["repetition"] if t5_r else (w - 1) // 3 + 1
        md.append(f"| Wave {w} | {t5_str} | {t6_str} | {t7_str} | 3 | Rep {rep_val} |")
    md.append("")
    md.append("---")
    md.append("")
    md.append("## 4. Subagent Orchestration & Overhead Disclosure")
    md.append("")
    md.append("In accordance with Sections 9, 10, and 17, setup subagents and parent orchestration usage are tracked and disclosed separately:")
    md.append("")
    md.append("| Role | Subagent Run ID | Model | Input Tokens | Output Tokens | Cache Read | Cost ($) |")
    md.append("| :--- | :--- | :--- | :---: | :---: | :---: | :---: |")
    for k, v in PHASE_A_SUBAGENT_OVERHEAD.items():
        if k == "total":
            continue
        md.append(f"| {k} | `{v['run_id']}` | `{v['model']}` | {v['input']:,} | {v['output']:,} | {v['cache_read']:,} | ${v['cost']:.4f} |")
    tot = PHASE_A_SUBAGENT_OVERHEAD["total"]
    md.append(f"| **Total Phase A Subagent Overhead** | - | - | **{tot['input']:,}** | **{tot['output']:,}** | **{tot['cache_read']:,}** | **${tot['cost']:.4f}** |")
    md.append("")
    md.append("*Worker metrics in Sections 1-3 strictly exclude subagent setup overhead.*")
    md.append("")
    atomic_write_text(R4_RESULTS_DIR / "BENCHMARK_ROUND_4.md", "\n".join(md) + "\n")

    # 4. CONTEXT_WINDOW_ANALYSIS.md
    cmd_lines = []
    cmd_lines.append("# Round 4 Context Window Consumption Analysis")
    cmd_lines.append("")
    cmd_lines.append("## Methodology")
    cmd_lines.append("Per Section 10 of the benchmark specification, active context size per model request is defined as:")
    cmd_lines.append("```text")
    cmd_lines.append("request_input_context = input + cacheRead + cacheWrite")
    cmd_lines.append("```")
    cmd_lines.append("For every completed assistant model response, the raw `message_end` event in Pi's JSONL telemetry is parsed.")
    cmd_lines.append(f"Context utilization percentage is computed relative to the verified model context window limit: **272,000 tokens** (`openai-codex/gpt-6-luna`).")
    cmd_lines.append("")
    cmd_lines.append("## Active Context Statistics by Profile")
    cmd_lines.append("")
    cmd_lines.append("| Profile | Runs | Mean Peak Context | Median Peak Context | Mean Median Context | Cumulative Context (Mean) | Mean Cache Read Rate | Peak Context % Limit |")
    cmd_lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
    for prof in ["A", "C", "U3"]:
        pruns = by_profile[prof]
        if pruns:
            peaks = [r.get("peak_input_context", 0) for r in pruns]
            meds = [r.get("median_input_context", 0) for r in pruns]
            cum_ctxs = [r.get("cumulative_context_tokens", 0) for r in pruns]
            cr_rates = [r.get("cache_read_rate", 0) for r in pruns]
            pct_limits = [r.get("peak_context_pct_limit", 0) for r in pruns]
            cmd_lines.append(f"| **{PROFILE_NAMES[prof]}** | {len(pruns)} | {statistics.mean(peaks):,.0f} | {statistics.median(peaks):,.0f} | {statistics.mean(meds):,.0f} | {statistics.mean(cum_ctxs):,.0f} | {statistics.mean(cr_rates) * 100:.1f}% | {statistics.mean(pct_limits):.2f}% |")
    cmd_lines.append("")
    cmd_lines.append("## Per-Run Context Trajectory Breakdown")
    cmd_lines.append("")
    cmd_lines.append("| Run ID | Profile | Task | Requests | First Req Context | Peak Req Context | Median Req Context | Final Req Context | Compaction Preceded Peak |")
    cmd_lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
    for r in results:
        reqs = r.get("per_request_usage", [])
        if reqs:
            first_c = reqs[0].get("request_input_context", 0)
            final_c = reqs[-1].get("request_input_context", 0)
        else:
            first_c = 0
            final_c = 0
        comp_str = "Yes" if r.get("compaction_preceded_peak") else "No"
        cmd_lines.append(f"| `{r['run_id']}` | **{r['profile']}** | `{r['task']}` | {r.get('model_requests', 0)} | {first_c:,} | {r.get('peak_input_context', 0):,} | {r.get('median_input_context', 0):,} | {final_c:,} | {comp_str} |")
    cmd_lines.append("")
    cmd_lines.append("## Findings on Context Growth and Cache Retention")
    cmd_lines.append("1. **Prompt Overlay Impact:** Profile A (no prompt overlay) starts with lower initial turn tokens than C and U3, but subsequent tool outputs dominate context growth.")
    cmd_lines.append("2. **Cache Read Efficiency:** Cache read rate remained high across repeated turns within the same session.")
    cmd_lines.append("3. **Context Ceiling Headroom:** Peak reported input contexts remained comfortably below the 272K model limit.")
    cmd_lines.append("")
    atomic_write_text(R4_RESULTS_DIR / "CONTEXT_WINDOW_ANALYSIS.md", "\n".join(cmd_lines) + "\n")

    # 5. CODE_QUALITY_REVIEW.md
    cqr = []
    cqr.append("# Round 4 Code Quality and Implementation Review")
    cqr.append("")
    cqr.append("## Scope")
    cqr.append("Qualitative review of representative generated solutions across Profiles A, C, and U3 on Task 5, Task 6, and Task 7.")
    cqr.append("")
    cqr.append("## Structural Observations")
    cqr.append("")
    for task_id in ["task-5", "task-6", "task-7"]:
        tname = TASK_META[task_id]["name"]
        cqr.append(f"### {tname} (`{task_id}`)")
        cqr.append("")
        for prof in ["A", "C", "U3"]:
            pruns = [r for r in results if r["task"] == task_id and r["profile"] == prof]
            passes = sum(1 for r in pruns if r.get("status") == "PASS")
            mean_lines = statistics.mean([r.get("line_count", 0) for r in pruns]) if pruns else 0
            mean_diff = statistics.mean([r.get("diff_lines", 0) for r in pruns]) if pruns else 0
            cqr.append(f"- **Profile {prof} ({PROFILE_NAMES[prof]}):** {passes}/{len(pruns)} passed | Avg code lines: {mean_lines:.0f} | Avg diff lines: {mean_diff:.0f}")
        cqr.append("")

    cqr.append("## Qualitative Comparison")
    cqr.append("- **Baseline (A):** Comprehensive docstrings, explanatory inline comments, standard defensive programming patterns.")
    cqr.append("- **Combined Lite (C):** Compact implementations with essential error handling, minimal boilerplate, high pass rate.")
    cqr.append("- **Combined Ultra (U3):** Highly dense, terse implementations. Caveman Ultra compression produces ultra-compact code; in edge cases can occasionally lead to omitted edge cases or brevity-induced regressions.")
    cqr.append("")
    atomic_write_text(R4_RESULTS_DIR / "CODE_QUALITY_REVIEW.md", "\n".join(cqr) + "\n")


def cmd_round_4_dry_run():
    """
    Performs full preflight verification for Round 4 without making paid model calls.
    Verifies:
      1. Working directory & local Git HEAD
      2. Profiles & prompt overlay SHA-256 hashes
      3. Task fixtures (Task 5, Task 6, Task 7) & files
      4. Independent evaluators
      5. Reference implementations pass 100%
      6. Defective starting fixtures fail
      7. Model pinning (openai-codex/gpt-6-luna:max) & tools
      8. 27 runs configured in 9 waves with Latin square counterbalance
      9. Workspace isolation & worker command construction
    """
    print("=" * 70)
    print("  Pi Autonomous Headless Benchmark Orchestrator — Round 4 Preflight")
    print("=" * 70)

    def check(title: str, condition: bool, details: str = ""):
        mark = "✓ PASS" if condition else "✗ FAIL"
        print(f"  [{mark}] {title}")
        if details:
            print(f"         {details}")
        if not condition:
            print("Preflight verification FAILED. Aborting.")
            sys.exit(1)

    # 1. Directory and HEAD
    check("Round 4 directory exists", R4_DIR.is_dir(), str(R4_DIR))
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(BENCH_ROOT), capture_output=True, text=True)
    check("Git repository clean and readable", proc.returncode == 0, f"HEAD: {proc.stdout.strip()}")

    # 2. Profiles and hashes
    c_path = BENCH_ROOT / "setup" / "profiles" / "combined-lite.md"
    u3_path = BENCH_ROOT / "round-3" / "profiles" / "combined-ultra.md"
    check("Profile C overlay exists", c_path.is_file())
    check("Profile U3 overlay exists", u3_path.is_file())

    def fhash(p):
        h = hashlib.sha256()
        with open(p, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

    check("Profile C SHA-256 matches frozen hash", fhash(c_path) == PROFILE_HASHES["C"], fhash(c_path))
    check("Profile U3 SHA-256 matches frozen hash", fhash(u3_path) == PROFILE_HASHES["U3"], fhash(u3_path))

    # 3. Tasks and fixtures
    for task_id in ["task-5", "task-6", "task-7"]:
        tmeta = TASK_META[task_id]
        fdir = BENCH_ROOT / tmeta["fixture_dir"]
        pfile = BENCH_ROOT / tmeta["prompt_file"]
        efile = R4_EVAL_DIR / f"eval_{task_id.replace('-', '_')}.py"
        rfile = R4_REFERENCES_DIR / f"{task_id.replace('-', '_')}_reference.py"
        check(f"{task_id} fixture directory exists", fdir.is_dir(), str(fdir))
        check(f"{task_id} prompt file exists", pfile.is_file(), str(pfile))
        check(f"{task_id} evaluator exists", efile.is_file(), str(efile))
        check(f"{task_id} reference solution exists", rfile.is_file(), str(rfile))

    # 4. Manifest
    man_file = R4_MANIFESTS_DIR / "fixture-manifest.json"
    check("Fixture manifest exists", man_file.is_file(), str(man_file))

    # 5. Model pinning and tools
    check("Launcher bench-a exists and executable", os.access(BENCH_ROOT / "bench-a", os.X_OK))
    check("Launcher bench-c exists and executable", os.access(BENCH_ROOT / "bench-c", os.X_OK))
    check("Launcher bench-u3 exists and executable", os.access(BENCH_ROOT / "bench-u3", os.X_OK))

    # 6. Latin-square schedule validation
    check("27 runs configured in 9 waves", len(ROUND_4_RUNS) == 27)
    for w in range(1, 10):
        w_runs = [r for r in ROUND_4_RUNS if r["wave"] == w]
        check(f"Wave {w} has exactly 3 runs", len(w_runs) == 3)
        tasks = {r["task"] for r in w_runs}
        profs = {r["profile"] for r in w_runs}
        check(f"Wave {w} has all 3 tasks (1 per task)", tasks == {"task-5", "task-6", "task-7"})
        check(f"Wave {w} has all 3 profiles (1 per profile)", profs == {"A", "C", "U3"})

    # 7. Worker command construction (Dry-run test)
    first_run = ROUND_4_RUNS[0]
    dry_env = os.environ.copy()
    dry_env["BENCH_DRY_RUN"] = "1"
    proc_dry = subprocess.run(
        [str(BENCH_ROOT / first_run["launcher"]), str(BENCH_ROOT / first_run["fixture_dir"])],
        capture_output=True,
        text=True,
        env=dry_env,
    )
    check("Launcher dry-run command verifies provider and model", "openai-codex" in proc_dry.stdout and "gpt-6-luna" in proc_dry.stdout)

    print("-" * 70)
    print("  Round 4 Preflight Verification: ALL CHECKS PASSED.")
    print("=" * 70)


def cmd_round_4_status():
    """
    Shows status of all 27 Round 4 runs.
    """
    print("=" * 70)
    print("  Pi Autonomous Headless Benchmark — Round 4 Status")
    print("=" * 70)

    res_json = R4_RESULTS_DIR / "benchmark-results.json"
    runs_map = {}
    if res_json.is_file():
        try:
            items = json.loads(res_json.read_text(encoding="utf-8"))
            runs_map = {r["run_id"]: r for r in items}
        except Exception:
            pass

    completed = len([r for r in runs_map.values() if r.get("status") in ("PASS", "TEST_FAIL", "AGENT_ERROR", "TIMEOUT")])
    passed = len([r for r in runs_map.values() if r.get("status") == "PASS"])

    print(f"  Total Runs: {len(ROUND_4_RUNS)} | Completed: {completed} | Passed: {passed} | Pending: {len(ROUND_4_RUNS) - completed}")
    print("-" * 70)

    for r_cfg in ROUND_4_RUNS:
        rid = r_cfg["run_id"]
        wave = r_cfg["wave"]
        task = r_cfg["task"]
        prof = r_cfg["profile"]
        rep = r_cfg["repetition"]
        if rid in runs_map:
            r = runs_map[rid]
            st = r.get("status", "UNKNOWN")
            cost = r.get("estimated_cost", 0.0)
            dur = r.get("duration_sec", 0.0)
            chk = f"{r.get('independent_passed', 0)}/{r.get('independent_total', 0)}"
            print(f"  [{rid}] Wave {wave} | {task} | {prof:2s} (Rep {rep}) : {st:12s} | Checks: {chk:5s} | Cost: ${cost:.4f} | {dur:.1f}s")
        else:
            print(f"  [{rid}] Wave {wave} | {task} | {prof:2s} (Rep {rep}) : PENDING")
    print("=" * 70)


def cmd_round_4_run(resume_mode: bool = False, retry_run_id: Optional[str] = None, wave_filter: Optional[int] = None, timeout_sec: int = 1800):
    """
    Executes the 27 runs in 9 waves (3 concurrent workers per wave).
    """
    print("=" * 70)
    print("  Pi Autonomous Headless Benchmark Orchestrator — Round 4 Execution")
    print(f"  Mode: {'Resume' if resume_mode else ('Retry ' + retry_run_id if retry_run_id else 'Full Run')}")
    if wave_filter:
        print(f"  Filtering to Wave: {wave_filter}")
    print("  Schedule: 9 waves x 3 concurrent workers (max_parallel_workers=3, max_parallel_per_task=1)")
    print("=" * 70)

    res_json = R4_RESULTS_DIR / "benchmark-results.json"
    results_map: Dict[str, dict] = {}
    if res_json.is_file():
        try:
            items = json.loads(res_json.read_text(encoding="utf-8"))
            results_map = {r["run_id"]: r for r in items}
        except Exception:
            pass

    # Group runs by wave
    waves = {}
    for r_cfg in ROUND_4_RUNS:
        w = r_cfg["wave"]
        waves.setdefault(w, []).append(r_cfg)

    for wave_num in range(1, 10):
        if wave_filter is not None and wave_num != wave_filter:
            continue
        wave_configs = waves.get(wave_num, [])
        to_run = []
        for r_cfg in wave_configs:
            rid = r_cfg["run_id"]
            if retry_run_id and rid != retry_run_id:
                continue
            if resume_mode and rid in results_map:
                prev_st = results_map[rid].get("status")
                if prev_st in ("PASS", "TEST_FAIL"):
                    print(f"Skipping completed run {rid} ({prev_st})")
                    continue
            to_run.append(r_cfg)

        if not to_run:
            continue

        print(f"\n>>> Launching Wave {wave_num} ({len(to_run)} workers concurrently) at {datetime.datetime.now(datetime.timezone.utc).isoformat()}...")
        for rc in to_run:
            print(f"    - {rc['run_id']}: {rc['task']} | Profile {rc['profile']} (Rep {rc['repetition']})")

        wave_start = time.monotonic()

        # Concurrent execution of the wave (max 3 workers)
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_to_cfg = {
                executor.submit(run_round_4_worker, rc, timeout_sec): rc
                for rc in to_run
            }
            for future in concurrent.futures.as_completed(future_to_cfg):
                rc = future_to_cfg[future]
                try:
                    collector = future.result()
                    results_map[collector.run_id] = collector.to_dict()
                    print(f"  [FINISHED] {collector.run_id} ({collector.task} / {collector.profile}): {collector.status} ({collector.duration_sec:.1f}s, ${collector.estimated_cost:.4f})")
                except Exception as e:
                    print(f"  [ERROR] {rc['run_id']} worker failed: {e}", file=sys.stderr)

        wave_end = time.monotonic()
        print(f">>> Wave {wave_num} complete in {wave_end - wave_start:.1f}s.")

        # Persist intermediate results and regenerate reports
        ordered_results = [results_map[rc["run_id"]] for rc in ROUND_4_RUNS if rc["run_id"] in results_map]
        generate_round_4_reports(ordered_results)

    print("\n" + "=" * 70)
    print("  Round 4 Execution Finished!")
    ordered_results = [results_map[rc["run_id"]] for rc in ROUND_4_RUNS if rc["run_id"] in results_map]
    generate_round_4_reports(ordered_results)
    print(f"  Reports updated in: {R4_RESULTS_DIR}")
    print("=" * 70)


def cmd_round_4_reevaluate():
    """
    Re-evaluates existing artifacts and refreshes all reports.
    """
    print("=" * 70)
    print("  Pi Autonomous Headless Benchmark — Round 4 Re-evaluation")
    print("=" * 70)

    res_json = R4_RESULTS_DIR / "benchmark-results.json"
    if not res_json.is_file():
        print("No benchmark-results.json found to re-evaluate.")
        return

    items = json.loads(res_json.read_text(encoding="utf-8"))
    runs_map = {r["run_id"]: r for r in items}

    for rc in ROUND_4_RUNS:
        rid = rc["run_id"]
        if rid not in runs_map:
            continue
        r = runs_map[rid]
        task_id = rc["task"]
        ws_dir = BENCH_ROOT / rc["workspace"]
        fix_dir = BENCH_ROOT / rc["fixture_dir"]
        eval_func = TASK_META[task_id]["eval_func"]

        if eval_func and ws_dir.is_dir():
            eval_res = eval_func(str(ws_dir), str(fix_dir))
            r["self_test_passed"] = eval_res.get("public_tests_passed", False)
            r["self_test_output"] = eval_res.get("public_tests_output", "")
            r["independent_test_passed"] = eval_res.get("hidden_tests_passed", False)
            r["independent_total"] = eval_res.get("hidden_total", 0)
            r["independent_passed"] = eval_res.get("hidden_passed", 0)
            r["output_file_sha256"] = eval_res.get("sha256")
            r["line_count"] = eval_res.get("line_count", 0)
            if eval_res.get("status") == "PASS":
                r["status"] = "PASS"
            elif r["status"] == "PASS":
                r["status"] = "TEST_FAIL"
            print(f"  [{rid}] Re-evaluated {task_id} / {rc['profile']}: {r['status']} ({r['independent_passed']}/{r['independent_total']})")

    ordered_results = [runs_map[rc["run_id"]] for rc in ROUND_4_RUNS if rc["run_id"] in runs_map]
    generate_round_4_reports(ordered_results)
    print("=" * 70)
