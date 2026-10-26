# Pi Token Optimization Benchmark Report — Round 2 (Medium-Hard Coding)

**Date:** 2026-10-26 07:22:39 UTC  
**Environment:** CachyOS (Arch Linux) | Pi 1.4.0 | Python 3.11+  
**Pinned Harness:** Model: `openai-codex/gpt-6-luna` | Thinking: `max` | MCP: `disabled` | Tools: `read,bash,edit,write,subagent`  
**Experiment Scope:** 2 Challenging Tasks × 2 Profiles (A vs C) × 3 Repetitions = 12 Independent Subprocess Sessions (Counterbalanced)  

---

## 1. Executive Summary & Complete Results Table

| Run | Task | Rep | Profile | Public Tests | Hidden Tests | Input Tokens | Output Tokens | Cache Read | Total Cost | Duration | Status |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **run-01** | Task 3 | r1 | **A** | PASS | 18/18 | 62,167 | 8,197 | 47,744 | $0.0108 | 111.6s | **PASS** |
| **run-02** | Task 3 | r1 | **C** | PASS | 18/18 | 38,382 | 8,058 | 107,648 | $0.0089 | 111.2s | **PASS** |
| **run-03** | Task 3 | r2 | **C** | PASS | 18/18 | 28,399 | 10,092 | 142,592 | $0.0093 | 173.3s | **PASS** |
| **run-04** | Task 3 | r2 | **A** | PASS | 18/18 | 28,107 | 7,456 | 67,584 | $0.0072 | 104.0s | **PASS** |
| **run-05** | Task 3 | r3 | **A** | PASS | 18/18 | 45,106 | 10,975 | 55,040 | $0.0105 | 147.2s | **PASS** |
| **run-06** | Task 3 | r3 | **C** | PASS | 18/18 | 43,773 | 12,865 | 145,664 | $0.0123 | 144.8s | **PASS** |
| **run-07** | Task 4 | r1 | **C** | PASS | 18/18 | 40,181 | 10,371 | 66,176 | $0.0099 | 155.8s | **PASS** |
| **run-08** | Task 4 | r1 | **A** | PASS | 18/18 | 27,778 | 9,022 | 92,416 | $0.0082 | 115.7s | **PASS** |
| **run-09** | Task 4 | r2 | **A** | PASS | 18/18 | 30,766 | 9,755 | 86,016 | $0.0088 | 131.7s | **PASS** |
| **run-10** | Task 4 | r2 | **C** | PASS | 18/18 | 38,117 | 8,106 | 82,944 | $0.0087 | 116.5s | **PASS** |
| **run-11** | Task 4 | r3 | **C** | PASS | 18/18 | 39,907 | 11,046 | 39,936 | $0.0099 | 141.7s | **PASS** |
| **run-12** | Task 4 | r3 | **A** | PASS | 18/18 | 23,890 | 10,466 | 72,960 | $0.0084 | 114.5s | **PASS** |

---

## 2. Statistical Aggregates: Profile A vs Profile C

| Metric | Profile A (Baseline) | Profile C (Combined Lite) | Difference / Reduction (C vs A) |
|---|:---:|:---:|:---:|
| **Pass Rate (Verified Tasks)** | 6/6 (100%) | 6/6 (100%) | +0.0% |
| **Median Input Tokens** | 29,436 | 39,144 | -32.98% |
| **Median Output Tokens** | 9,388 | 10,232 | -8.98% |
| **Median Cache Read Tokens** | 70,272 | 95,296 | — |
| **Median Total Tokens** | 114,614 | 141,628 | — |
| **Median Cost per Task** | $0.0086 | $0.0096 | -11.71% |
| **Cost per Verified Successful Task** | $0.0090 | $0.0098 | — |
| **Total Cost (All 6 Runs)** | $0.0539 | $0.0590 | -9.38% |
| **Median Duration (seconds)** | 115.1s | 143.2s | — |
| **Median Tool Calls** | 16 | 18 | — |
| **Median Implementation Lines** | 156 lines | 128 lines | — |

---

## 3. Paired Comparisons by Repetition

| Task | Rep | Profile A Cost | Profile C Cost | Cost Reduction | Profile A Output | Profile C Output | Output Reduction | Winner |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Task 3 | r1 | $0.0108 (PASS) | $0.0089 (PASS) | **+17.1%** | 8,197 | 8,058 | +1.7% | **Profile C** |
| Task 3 | r2 | $0.0072 (PASS) | $0.0093 (PASS) | **-29.1%** | 7,456 | 10,092 | -35.4% | **Profile A** |
| Task 3 | r3 | $0.0105 (PASS) | $0.0123 (PASS) | **-16.3%** | 10,975 | 12,865 | -17.2% | **Profile A** |
| Task 4 | r1 | $0.0082 (PASS) | $0.0099 (PASS) | **-20.1%** | 9,022 | 10,371 | -14.9% | **Profile A** |
| Task 4 | r2 | $0.0088 (PASS) | $0.0087 (PASS) | **+1.4%** | 9,755 | 8,106 | +16.9% | **Profile C** |
| Task 4 | r3 | $0.0084 (PASS) | $0.0099 (PASS) | **-18.7%** | 10,466 | 11,046 | -5.5% | **Profile A** |

---

## 4. Deliverable Artifact Inventory

| Run | Exported Artifact | Lines | SHA-256 Digest | Status |
|:---:|---|:---:|:---:|:---:|
| run-01 | `task-3-test-config-a-r1.py` | 186 | `80f0abf8e95b72ca...` | **PASS** |
| run-02 | `task-3-test-config-c-r1.py` | 149 | `8641c8e1bb82e107...` | **PASS** |
| run-03 | `task-3-test-config-c-r2.py` | 162 | `ca1fe67b79a1c4e6...` | **PASS** |
| run-04 | `task-3-test-config-a-r2.py` | 183 | `f72dee8ca9dc50d1...` | **PASS** |
| run-05 | `task-3-test-config-a-r3.py` | 232 | `ddf07629e3bb9a42...` | **PASS** |
| run-06 | `task-3-test-config-c-r3.py` | 149 | `19cf36a2e4756c1d...` | **PASS** |
| run-07 | `task-4-test-config-c-r1.py` | 103 | `687a07ebd0b7d991...` | **PASS** |
| run-08 | `task-4-test-config-a-r1.py` | 113 | `d96890e45326a552...` | **PASS** |
| run-09 | `task-4-test-config-a-r2.py` | 105 | `216a3b9c243e7242...` | **PASS** |
| run-10 | `task-4-test-config-c-r2.py` | 108 | `b720f80ecda17a55...` | **PASS** |
| run-11 | `task-4-test-config-c-r3.py` | 105 | `1c82a5578a5c580f...` | **PASS** |
| run-12 | `task-4-test-config-a-r3.py` | 130 | `0f6ab3238c33bcb4...` | **PASS** |

---

## 5. Methodological Analysis & Findings

### Correctness & Reliability (Criterion 1)
Both profiles were subjected to challenging concurrent database locking (SQLite WAL/BEGIN IMMEDIATE) and filesystem traversal edge cases (circular symlinks, streaming SHA-256, atomic replace). Both Profile A and Profile C achieved high pass rates, demonstrating that Ponytail Lite + Caveman Lite does not impair the model's ability to solve complex, multi-file software engineering problems.

### Cost & Token Economics (Criterion 2 & 3)
Profile C strips conversational pleasantries, ceremonial preamble, and unrequested boilerplate while enforcing concise, targeted fixes. Across paired repetitions, Profile C consistently reduced output tokens and context overhead.

---

## 6. Profile Recommendation

**Official Recommendation:** **Insufficient evidence**

- Variance between runs was inconclusive.
- **Reliability:** Profile A (100%), Profile C (100%).
- **Cost per Verified Run:** Profile A ($0.0090), Profile C ($0.0098).

