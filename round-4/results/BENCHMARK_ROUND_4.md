# Autonomous Pi Token Optimization Benchmark Report — Round 4 Direct Comparison

**Experiment:** Headless Pi Benchmark Round 4 (Direct Baseline vs Lite vs Ultra)
**Date:** 2026-10-26 19:22:26 UTC
**Model:** `openai-codex / gpt-6-luna` | **Thinking:** `max` | **Tools:** `read,bash,edit,write,subagent`
**Harness:** Headless JSON mode (`pi --mode json`), fresh sessions, isolated process groups
**Design:** 3 tasks x 3 profiles x 3 repetitions = 27 independent benchmark runs in 9 waves
**Concurrency:** `max_parallel_workers=3`, `max_parallel_per_task=1` (Counterbalanced Latin square)

---

## 1. Executive Summary & Recommendation

| Metric | Profile A (Baseline) | Profile C (Combined Lite) | Profile U3 (Combined Ultra) | Lite vs Base | Ultra vs Base |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Pass Rate** | 100.0% (9/9) | 100.0% (9/9) | 100.0% (9/9) | +0.0% pts | +0.0% pts |
| **Total Cost** | $0.1197 | $0.0978 | $0.1023 | -18.3% | -14.5% |
| **Mean Cost / Run** | $0.0133 | $0.0109 | $0.0114 | -18.3% | -14.5% |
| **Median Cost / Run** | $0.0136 | $0.0103 | $0.0111 | -24.2% | -18.6% |
| **Mean Duration** | 194.1s | 176.2s | 167.5s | -9.3% | -13.7% |
| **Mean Input Tokens** | 36,724 | 32,763 | 31,097 | -10.8% | -15.3% |
| **Mean Output Tokens** | 16,076 | 12,986 | 13,802 | -19.2% | -14.1% |
| **Mean Cache-Read Tokens** | 158,407 | 109,255 | 135,922 | -31.0% | -14.2% |
| **Mean Peak Input Context** | 26,699 | 21,592 | 23,107 | -19.1% | -13.5% |
| **Mean Model Requests** | 10.8 | 9.3 | 10.4 | -13.4% | -3.1% |
| **Mean Diff Lines** | 298.3 | 266.0 | 257.0 | -10.8% | -13.9% |

### Evidence-Backed Recommendation: **Profile C (Combined Lite)**

Key findings:
- **Profile C (Lite)** achieved 100.0% pass rate with total cost $0.0978.
- **Profile U3 (Ultra)** achieved 100.0% pass rate with total cost $0.1023.
- **Profile A (Baseline)** achieved 100.0% pass rate with total cost $0.1197.

---

## 2. Per-Task Paired Repetition Results

### Streaming HTTP Chunked Decoder (`task-5`)

| Run ID | Wave | Profile | Repetition | Status | Checks Passed | Duration (s) | Cost ($) | Peak Context | Output Tokens | Diff Lines |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `run-01` | 1 | **A** | Rep 1 | `PASS` | 24/24 | 251.4s | $0.0170 | 34,186 | 20,855 | 371 |
| `run-04` | 2 | **C** | Rep 1 | `PASS` | 24/24 | 285.3s | $0.0159 | 28,702 | 18,943 | 310 |
| `run-07` | 3 | **U3** | Rep 1 | `PASS` | 24/24 | 208.6s | $0.0130 | 23,622 | 17,972 | 306 |
| `run-16` | 6 | **A** | Rep 2 | `PASS` | 24/24 | 197.4s | $0.0136 | 27,606 | 19,017 | 346 |
| `run-10` | 4 | **C** | Rep 2 | `PASS` | 24/24 | 230.9s | $0.0128 | 23,484 | 15,684 | 286 |
| `run-13` | 5 | **U3** | Rep 2 | `PASS` | 24/24 | 180.6s | $0.0134 | 28,833 | 16,430 | 313 |
| `run-22` | 8 | **A** | Rep 3 | `PASS` | 24/24 | 266.0s | $0.0158 | 32,257 | 21,698 | 314 |
| `run-25` | 9 | **C** | Rep 3 | `PASS` | 24/24 | 243.2s | $0.0147 | 28,620 | 19,806 | 297 |
| `run-19` | 7 | **U3** | Rep 3 | `PASS` | 24/24 | 241.8s | $0.0158 | 27,941 | 18,685 | 215 |

### Fault-Tolerant Concurrent Job Scheduler (`task-6`)

| Run ID | Wave | Profile | Repetition | Status | Checks Passed | Duration (s) | Cost ($) | Peak Context | Output Tokens | Diff Lines |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `run-08` | 3 | **A** | Rep 1 | `PASS` | 22/22 | 109.6s | $0.0078 | 18,286 | 10,344 | 154 |
| `run-02` | 1 | **C** | Rep 1 | `PASS` | 22/22 | 89.1s | $0.0068 | 14,650 | 8,557 | 127 |
| `run-05` | 2 | **U3** | Rep 1 | `PASS` | 22/22 | 102.1s | $0.0062 | 14,880 | 8,213 | 119 |
| `run-14` | 5 | **A** | Rep 2 | `PASS` | 22/22 | 113.5s | $0.0085 | 16,168 | 7,333 | 141 |
| `run-17` | 6 | **C** | Rep 2 | `PASS` | 22/22 | 158.6s | $0.0090 | 19,444 | 8,652 | 126 |
| `run-11` | 4 | **U3** | Rep 2 | `PASS` | 22/22 | 113.6s | $0.0090 | 16,595 | 9,009 | 119 |
| `run-20` | 7 | **A** | Rep 3 | `PASS` | 22/22 | 112.9s | $0.0079 | 16,748 | 9,387 | 132 |
| `run-23` | 8 | **C** | Rep 3 | `PASS` | 22/22 | 137.6s | $0.0079 | 14,428 | 8,453 | 110 |
| `run-26` | 9 | **U3** | Rep 3 | `PASS` | 22/22 | 171.2s | $0.0108 | 20,538 | 11,665 | 121 |

### Crash-Safe Event-Sourced Ledger (`task-7`)

| Run ID | Wave | Profile | Repetition | Status | Checks Passed | Duration (s) | Cost ($) | Peak Context | Output Tokens | Diff Lines |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `run-06` | 2 | **A** | Rep 1 | `PASS` | 22/22 | 148.7s | $0.0122 | 28,966 | 14,416 | 413 |
| `run-09` | 3 | **C** | Rep 1 | `PASS` | 22/22 | 136.5s | $0.0097 | 21,412 | 12,858 | 388 |
| `run-03` | 1 | **U3** | Rep 1 | `PASS` | 22/22 | 141.7s | $0.0106 | 22,123 | 13,803 | 376 |
| `run-12` | 4 | **A** | Rep 2 | `PASS` | 22/22 | 284.1s | $0.0189 | 32,357 | 19,740 | 410 |
| `run-15` | 5 | **C** | Rep 2 | `PASS` | 22/22 | 166.5s | $0.0103 | 21,318 | 13,080 | 372 |
| `run-18` | 6 | **U3** | Rep 2 | `PASS` | 22/22 | 133.3s | $0.0111 | 25,092 | 13,291 | 364 |
| `run-27` | 9 | **A** | Rep 3 | `PASS` | 22/22 | 263.6s | $0.0179 | 33,720 | 21,895 | 404 |
| `run-21` | 7 | **C** | Rep 3 | `PASS` | 22/22 | 137.7s | $0.0107 | 22,268 | 10,841 | 378 |
| `run-24` | 8 | **U3** | Rep 3 | `PASS` | 22/22 | 214.4s | $0.0124 | 28,337 | 15,154 | 380 |

---

## 3. Wave Execution Schedule & Overlapping Concurrency

Execution was conducted in 9 sequential waves of up to 3 concurrent workers:

| Wave | Task 5 Worker | Task 6 Worker | Task 7 Worker | Wave Concurrency | Repetition |
| :---: | :---: | :---: | :---: | :---: | :---: |
| Wave 1 | run-01 (A: PASS) | run-02 (C: PASS) | run-03 (U3: PASS) | 3 | Rep 1 |
| Wave 2 | run-04 (C: PASS) | run-05 (U3: PASS) | run-06 (A: PASS) | 3 | Rep 1 |
| Wave 3 | run-07 (U3: PASS) | run-08 (A: PASS) | run-09 (C: PASS) | 3 | Rep 1 |
| Wave 4 | run-10 (C: PASS) | run-11 (U3: PASS) | run-12 (A: PASS) | 3 | Rep 2 |
| Wave 5 | run-13 (U3: PASS) | run-14 (A: PASS) | run-15 (C: PASS) | 3 | Rep 2 |
| Wave 6 | run-16 (A: PASS) | run-17 (C: PASS) | run-18 (U3: PASS) | 3 | Rep 2 |
| Wave 7 | run-19 (U3: PASS) | run-20 (A: PASS) | run-21 (C: PASS) | 3 | Rep 3 |
| Wave 8 | run-22 (A: PASS) | run-23 (C: PASS) | run-24 (U3: PASS) | 3 | Rep 3 |
| Wave 9 | run-25 (C: PASS) | run-26 (U3: PASS) | run-27 (A: PASS) | 3 | Rep 3 |

---

## 4. Subagent Orchestration & Overhead Disclosure

In accordance with Sections 9, 10, and 17, setup subagents and parent orchestration usage are tracked and disclosed separately:

| Role | Subagent Run ID | Model | Input Tokens | Output Tokens | Cache Read | Cost ($) |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: |
| task-5-architect | `aeff2c5f` | `antigravity/gemini-3.8-flash:low` | 319,337 | 15,088 | 161,312 | $0.0420 |
| task-6-architect | `3df382c1` | `antigravity/gemini-3.8-flash:low` | 381,128 | 16,013 | 272,362 | $0.0513 |
| task-7-architect | `9089b9b2` | `antigravity/gemini-3.8-flash:low` | 137,574 | 19,225 | 958,761 | $0.0454 |
| **Total Phase A Subagent Overhead** | - | - | **838,039** | **50,326** | **1,392,435** | **$0.1387** |

*Worker metrics in Sections 1-3 strictly exclude subagent setup overhead.*

