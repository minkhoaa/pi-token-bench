# Pi Token Optimization Benchmark Report

**Date:** 2026-10-26 05:32:54 UTC  
**Environment:** CachyOS (Arch Linux) | Pi 1.4.0 | Python 3.11+  
**Pinned Harness:** Model: `openai-codex/gpt-6-luna` | Thinking: `max` | MCP: `disabled` | Tools: `read,bash,edit,write,subagent`  

---

## 1. Executive Summary & Results Table

| Task | Profile | Tests | Input | Output | Cache Read | Cost | Duration | Tool Calls | Status |
|---|---|---|---|---|---|---|---|---|---|
| Task 1 | A | 10/10 | 28,425 | 19,356 | 196,096 | $0.0145 | 204.5s | 14 | **PASS** |
| Task 1 | B | 10/10 | 24,086 | 8,517 | 47,360 | $0.0071 | 102.8s | 10 | **PASS** |
| Task 1 | C | 10/10 | 12,489 | 7,948 | 48,384 | $0.0057 | 83.8s | 10 | **PASS** |
| Task 1 | D | 10/10 | 16,746 | 13,886 | 49,408 | $0.0091 | 156.8s | 8 | **PASS** |
| Task 2 | A | 8/8 | 17,017 | 9,601 | 27,520 | $0.0068 | 105.7s | 5 | **PASS** |
| Task 2 | B | 8/8 | 33,829 | 12,281 | 117,632 | $0.0107 | 135.0s | 12 | **PASS** |
| Task 2 | C | 8/8 | 17,127 | 9,385 | 28,288 | $0.0067 | 110.5s | 6 | **PASS** |
| Task 2 | D | 8/8 | 16,829 | 11,818 | 84,992 | $0.0084 | 113.9s | 8 | **PASS** |

---

## 2. Profile Aggregates & Comparative Analysis

| Profile | Name | Tasks Passed | Pass Rate | Total Tokens | Input Tokens | Output Tokens | Cache Read | Total Cost | Duration | Cost/Task | Cost Red. vs A |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | A — Baseline | 2/2 | 100% | 298,015 | 45,442 | 28,957 | 223,616 | $0.0213 | 310.2s | $0.0106 | 0.0% (ref) |
| B | B — Minimal | 2/2 | 100% | 243,705 | 57,915 | 20,798 | 164,992 | $0.0178 | 237.8s | $0.0089 | +16.1% |
| C | C — Terse | 2/2 | 100% | 123,621 | 29,616 | 17,333 | 76,672 | $0.0124 | 194.2s | $0.0062 | +41.7% |
| D | D — Optimized | 2/2 | 100% | 193,679 | 33,575 | 25,704 | 134,400 | $0.0176 | 270.7s | $0.0088 | +17.4% |

### Relative Reduction Metrics vs Baseline (Profile A)

| Metric | Profile B (Ponytail) | Profile C (Combined Lite) | Profile D (Optimized + RTK) |
|---|---|---|---|
| **Input Token Reduction** | -27.45% | +34.83% | +26.11% |
| **Output Token Reduction** | +28.18% | +40.14% | +11.23% |
| **Total Token Reduction** | +18.22% | +58.52% | +35.01% |
| **Cost Reduction** | +16.07% | +41.69% | +17.42% |

---

## 3. RTK Compression Telemetry (Profile D)

RTK interceptor savings are reported separately from provider-reported tokens to avoid conflating shell command compression with LLM context pricing:

- **Total Intercepted Commands:** 7
- **Raw CLI Output Chars/Bytes:** 88
- **Filtered Output to Context:** 13
- **Estimated Tokens Saved by RTK:** 75
- **Average Output Reduction:** 85.2%

---

## 4. Deliverable Artifacts & Code Verification

| Run | Target File | Line Count | SHA-256 | Self-Test | External Test |
|---|---|---|---|---|---|
| run-01 | `task-1-test-config-a.py` | 261 | `6e7dcf223691...` | PASS | 10/10 |
| run-02 | `task-1-test-config-b.py` | 168 | `41a9bc9bd8b8...` | PASS | 10/10 |
| run-03 | `task-1-test-config-c.py` | 198 | `afa984ba95e3...` | PASS | 10/10 |
| run-04 | `task-1-test-config-d.py` | 181 | `61ab55b7e0f9...` | PASS | 10/10 |
| run-05 | `task-2-test-config-a.py` | 398 | `d79d17b9977a...` | PASS | 8/8 |
| run-06 | `task-2-test-config-b.py` | 338 | `98bb247dd27a...` | PASS | 8/8 |
| run-07 | `task-2-test-config-c.py` | 334 | `1f694468ede5...` | PASS | 8/8 |
| run-08 | `task-2-test-config-d.py` | 339 | `4f1fe3c5ff62...` | PASS | 8/8 |

---

## 5. Methodological Notes & Exploratory Nature

1. **Exploratory Scope:** This initial run evaluates one repetition per task/profile (N=1). Results are exploratory rather than statistically definitive. Variance across reasoning trajectories is typical for high-thinking models.
2. **Cache Reads vs Context Occupancy:** Provider-reported cache read tokens reflect prompt prefix caching hits, not context window saturation.
3. **Zero Contamination:** All runs executed in strictly isolated task directories with byte-identical canonical task prompts and clean session stores.

---

## 6. Profile Recommendations (Correctness First, Cost Second)

**Recommended Profile:** **Profile C (C — Terse)**

- **Correctness:** 2/2 tasks passed (100% pass rate).
- **Cost per Successful Task:** $0.0062 (Total cost: $0.0124).
- **Cost Reduction vs Baseline A:** +41.69%.

