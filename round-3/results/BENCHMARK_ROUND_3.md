# Autonomous Pi Token Optimization Benchmark Report — Round 3 ULTRA

**Date:** 2026-10-26 09:57:26 UTC  
**Environment:** CachyOS (Linux) | Pi Coding Agent 1.4.0 | Python 3.11+  
**Pinned Harness:** Provider: `openai-codex` | Model: `gpt-6-luna` | Thinking: `max` | MCP: `disabled` | Tools: `read,bash,edit,write,subagent`  
**Scope:** 2 Canonical Tasks × 6 Profiles (A, C, U1, U2, U3, U4) × 3 Repetitions = 36 Independent Headless Sessions (Counterbalanced)  

---

## 1. Executive Summary & Full Results Table

| Run | Task | Rep | Profile | Status | Public Tests | Hidden Tests | Input Tokens | Output Tokens | Cache Read | Total Cost | Duration | Lines |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **run-01** | Task 3 | r1 | **A** | **PASS** | PASS | 18/18 | 23,373 | 10,728 | 163,456 | $0.0093 | 128.3s | 198 |
| **run-02** | Task 3 | r1 | **U1** | **PASS** | PASS | 18/18 | 25,066 | 6,913 | 100,992 | $0.0070 | 92.4s | 142 |
| **run-03** | Task 3 | r1 | **C** | **PASS** | PASS | 18/18 | 21,047 | 8,783 | 126,208 | $0.0078 | 107.3s | 144 |
| **run-04** | Task 3 | r1 | **U2** | **PASS** | PASS | 18/18 | 27,013 | 15,452 | 181,248 | $0.0122 | 161.4s | 199 |
| **run-05** | Task 3 | r1 | **U3** | **PASS** | PASS | 18/18 | 59,128 | 9,466 | 96,128 | $0.0116 | 125.3s | 154 |
| **run-06** | Task 3 | r1 | **U4** | **PASS** | PASS | 18/18 | 21,837 | 7,508 | 55,168 | $0.0065 | 118.1s | 156 |
| **run-07** | Task 4 | r1 | **U4** | **PASS** | PASS | 18/18 | 20,451 | 10,767 | 166,016 | $0.0091 | 180.2s | 92 |
| **run-08** | Task 4 | r1 | **U3** | **PASS** | PASS | 18/18 | 25,251 | 9,890 | 67,200 | $0.0081 | 115.6s | 99 |
| **run-09** | Task 4 | r1 | **U2** | **PASS** | PASS | 18/18 | 28,523 | 9,049 | 54,912 | $0.0079 | 110.3s | 116 |
| **run-10** | Task 4 | r1 | **C** | **PASS** | PASS | 18/18 | 40,506 | 7,807 | 31,104 | $0.0083 | 116.6s | 106 |
| **run-11** | Task 4 | r1 | **U1** | **PASS** | PASS | 18/18 | 61,237 | 10,639 | 106,240 | $0.0125 | 173.1s | 102 |
| **run-12** | Task 4 | r1 | **A** | **PASS** | PASS | 18/18 | 22,420 | 7,262 | 40,960 | $0.0063 | 107.1s | 125 |
| **run-13** | Task 3 | r2 | **U2** | **PASS** | PASS | 18/18 | 26,963 | 11,336 | 126,720 | $0.0096 | 152.0s | 202 |
| **run-14** | Task 3 | r2 | **U3** | **PASS** | PASS | 18/18 | 34,479 | 8,324 | 90,112 | $0.0085 | 105.1s | 160 |
| **run-15** | Task 3 | r2 | **U4** | **PASS** | PASS | 18/18 | 34,618 | 7,863 | 70,272 | $0.0081 | 87.7s | 166 |
| **run-16** | Task 3 | r2 | **A** | **PASS** | PASS | 18/18 | 23,191 | 10,035 | 157,824 | $0.0089 | 126.3s | 215 |
| **run-17** | Task 3 | r2 | **U1** | **PASS** | PASS | 18/18 | 21,053 | 8,703 | 111,104 | $0.0076 | 151.5s | 142 |
| **run-18** | Task 3 | r2 | **C** | **PASS** | PASS | 18/18 | 29,288 | 10,357 | 86,400 | $0.0090 | 121.0s | 166 |
| **run-19** | Task 4 | r2 | **C** | **PASS** | PASS | 18/18 | 24,184 | 9,482 | 62,848 | $0.0078 | 120.1s | 116 |
| **run-20** | Task 4 | r2 | **U1** | **PASS** | PASS | 18/18 | 43,234 | 10,930 | 72,192 | $0.0105 | 166.0s | 84 |
| **run-21** | Task 4 | r2 | **A** | **PASS** | PASS | 18/18 | 29,282 | 8,788 | 40,064 | $0.0077 | 125.8s | 120 |
| **run-22** | Task 4 | r2 | **U4** | **PASS** | PASS | 18/18 | 20,669 | 11,054 | 75,008 | $0.0083 | 120.3s | 97 |
| **run-23** | Task 4 | r2 | **U3** | **PASS** | PASS | 18/18 | 30,936 | 10,550 | 104,576 | $0.0094 | 133.4s | 110 |
| **run-24** | Task 4 | r2 | **U2** | **PASS** | PASS | 18/18 | 37,630 | 10,791 | 92,160 | $0.0101 | 177.8s | 111 |
| **run-25** | Task 3 | r3 | **U3** | **PASS** | PASS | 18/18 | 34,309 | 7,229 | 85,888 | $0.0079 | 125.5s | 155 |
| **run-26** | Task 3 | r3 | **U4** | **PASS** | PASS | 18/18 | 24,971 | 9,511 | 117,248 | $0.0084 | 155.5s | 158 |
| **run-27** | Task 3 | r3 | **A** | **PASS** | PASS | 18/18 | 28,533 | 8,665 | 102,272 | $0.0082 | 111.5s | 185 |
| **run-28** | Task 3 | r3 | **U1** | **PASS** | PASS | 18/18 | 47,214 | 9,891 | 81,920 | $0.0105 | 148.9s | 147 |
| **run-29** | Task 3 | r3 | **C** | **PASS** | PASS | 18/18 | 45,796 | 12,797 | 142,464 | $0.0124 | 150.1s | 166 |
| **run-30** | Task 3 | r3 | **U2** | **PASS** | PASS | 18/18 | 51,491 | 12,547 | 103,168 | $0.0125 | 160.9s | 196 |
| **run-31** | Task 4 | r3 | **U1** | **PASS** | PASS | 18/18 | 21,344 | 9,524 | 54,272 | $0.0074 | 128.9s | 92 |
| **run-32** | Task 4 | r3 | **C** | **PASS** | PASS | 18/18 | 46,665 | 10,106 | 112,000 | $0.0108 | 148.6s | 98 |
| **run-33** | Task 4 | r3 | **U2** | **PASS** | PASS | 18/18 | 41,934 | 11,422 | 63,744 | $0.0105 | 169.6s | 124 |
| **run-34** | Task 4 | r3 | **U3** | **PASS** | PASS | 18/18 | 36,326 | 10,036 | 96,640 | $0.0096 | 120.9s | 104 |
| **run-35** | Task 4 | r3 | **U4** | **PASS** | PASS | 18/18 | 28,169 | 11,148 | 74,496 | $0.0091 | 134.9s | 98 |
| **run-36** | Task 4 | r3 | **A** | **PASS** | PASS | 18/18 | 20,949 | 9,514 | 39,040 | $0.0072 | 127.4s | 121 |

---

## 2. Statistical Aggregates & Profile Comparison

| Metric | Profile A (Baseline) | Profile C (Combined Lite) | Profile U1 (Ponytail Ultra) | Profile U2 (Caveman Ultra) | Profile U3 (Combined Ultra) | Profile U4 (Combined Ultra+RTK) |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **Pass Rate (Verified)** | 6/6 (100.0%) | 6/6 (100.0%) | 6/6 (100.0%) | 6/6 (100.0%) | 6/6 (100.0%) | 6/6 (100.0%) |
| **Median Input Tokens** | 23,282.0 | 34,897.0 | 34,150.0 | 33,076.5 | 34,394.0 | 23,404.0 |
| **Median Output Tokens** | 9,151.0 | 9,794.0 | 9,707.5 | 11,379.0 | 9,678.0 | 10,139.0 |
| **Median Cache Read** | 71,616.0 | 99,200.0 | 91,456.0 | 97,664.0 | 93,120.0 | 74,752.0 |
| **Median Total Tokens** | 108,802.0 | 141,041.5 | 135,998.0 | 152,800.0 | 137,958.5 | 113,283.0 |
| **Median Cost per Task** | $0.0080 | $0.0086 | $0.0090 | $0.0103 | $0.0090 | $0.0084 |
| **Mean Cost per Task** | $0.0080 | $0.0093 | $0.0092 | $0.0105 | $0.0092 | $0.0083 |
| **Total Cost (All 6 Runs)** | $0.0477 | $0.0560 | $0.0555 | $0.0629 | $0.0552 | $0.0496 |
| **Cost / Verified Pass** | $0.0080 | $0.0093 | $0.0092 | $0.0105 | $0.0092 | $0.0083 |
| **Median Duration** | 126.0s | 120.5s | 150.2s | 161.2s | 123.1s | 127.6s |
| **Median Tool Calls** | 15.5 | 18.5 | 18.0 | 16.5 | 17.0 | 16.0 |
| **Median Solution Lines** | 155.0 | 130.0 | 122.0 | 160.0 | 132.0 | 127.0 |
| **Median Diff Lines** | 169.0 | 128.5 | 116.5 | 133.0 | 162.0 | 122.5 |
| **Ponytail Omissions** | 0 | 0 | 0 | 0 | 0 | 0 |
| **Caveman Truncations** | 0 | 0 | 0 | 0 | 0 | 0 |

---

## 3. Paired Comparisons by Repetition

### Paired Comparisons vs Baseline Profile A

| Task | Rep | Comparison | Baseline A Cost | Target Cost | Cost Delta (%) | Baseline A Output | Target Output | Output Delta (%) | Winner |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| task-3 | r1 | **C vs A** | $0.0093 | $0.0078 | +16.9% | 10,728 | 8,783 | +18.1% | **C** |
| task-3 | r1 | **U1 vs A** | $0.0093 | $0.0070 | +25.3% | 10,728 | 6,913 | +35.6% | **U1** |
| task-3 | r1 | **U2 vs A** | $0.0093 | $0.0122 | -31.1% | 10,728 | 15,452 | -44.0% | **A** |
| task-3 | r1 | **U3 vs A** | $0.0093 | $0.0116 | -24.3% | 10,728 | 9,466 | +11.8% | **A** |
| task-3 | r1 | **U4 vs A** | $0.0093 | $0.0065 | +30.5% | 10,728 | 7,508 | +30.0% | **U4** |
| task-3 | r2 | **C vs A** | $0.0089 | $0.0090 | -0.6% | 10,035 | 10,357 | -3.2% | **A** |
| task-3 | r2 | **U1 vs A** | $0.0089 | $0.0076 | +15.1% | 10,035 | 8,703 | +13.3% | **U1** |
| task-3 | r2 | **U2 vs A** | $0.0089 | $0.0096 | -8.0% | 10,035 | 11,336 | -13.0% | **A** |
| task-3 | r2 | **U3 vs A** | $0.0089 | $0.0085 | +4.5% | 10,035 | 8,324 | +17.1% | **U3** |
| task-3 | r2 | **U4 vs A** | $0.0089 | $0.0081 | +9.2% | 10,035 | 7,863 | +21.6% | **U4** |
| task-3 | r3 | **C vs A** | $0.0082 | $0.0124 | -51.1% | 8,665 | 12,797 | -47.7% | **A** |
| task-3 | r3 | **U1 vs A** | $0.0082 | $0.0105 | -27.7% | 8,665 | 9,891 | -14.2% | **A** |
| task-3 | r3 | **U2 vs A** | $0.0082 | $0.0125 | -51.7% | 8,665 | 12,547 | -44.8% | **A** |
| task-3 | r3 | **U3 vs A** | $0.0082 | $0.0079 | +3.7% | 8,665 | 7,229 | +16.6% | **U3** |
| task-3 | r3 | **U4 vs A** | $0.0082 | $0.0084 | -2.6% | 8,665 | 9,511 | -9.8% | **A** |
| task-4 | r1 | **C vs A** | $0.0063 | $0.0083 | -31.6% | 7,262 | 7,807 | -7.5% | **A** |
| task-4 | r1 | **U1 vs A** | $0.0063 | $0.0125 | -99.0% | 7,262 | 10,639 | -46.5% | **A** |
| task-4 | r1 | **U2 vs A** | $0.0063 | $0.0079 | -26.1% | 7,262 | 9,049 | -24.6% | **A** |
| task-4 | r1 | **U3 vs A** | $0.0063 | $0.0081 | -29.6% | 7,262 | 9,890 | -36.2% | **A** |
| task-4 | r1 | **U4 vs A** | $0.0063 | $0.0091 | -44.7% | 7,262 | 10,767 | -48.3% | **A** |
| task-4 | r2 | **C vs A** | $0.0077 | $0.0078 | -0.8% | 8,788 | 9,482 | -7.9% | **A** |
| task-4 | r2 | **U1 vs A** | $0.0077 | $0.0105 | -36.1% | 8,788 | 10,930 | -24.4% | **A** |
| task-4 | r2 | **U2 vs A** | $0.0077 | $0.0101 | -30.5% | 8,788 | 10,791 | -22.8% | **A** |
| task-4 | r2 | **U3 vs A** | $0.0077 | $0.0094 | -21.9% | 8,788 | 10,550 | -20.1% | **A** |
| task-4 | r2 | **U4 vs A** | $0.0077 | $0.0083 | -8.0% | 8,788 | 11,054 | -25.8% | **A** |
| task-4 | r3 | **C vs A** | $0.0072 | $0.0108 | -49.7% | 9,514 | 10,106 | -6.2% | **A** |
| task-4 | r3 | **U1 vs A** | $0.0072 | $0.0074 | -2.7% | 9,514 | 9,524 | -0.1% | **A** |
| task-4 | r3 | **U2 vs A** | $0.0072 | $0.0105 | -45.6% | 9,514 | 11,422 | -20.1% | **A** |
| task-4 | r3 | **U3 vs A** | $0.0072 | $0.0096 | -32.8% | 9,514 | 10,036 | -5.5% | **A** |
| task-4 | r3 | **U4 vs A** | $0.0072 | $0.0091 | -26.1% | 9,514 | 11,148 | -17.2% | **A** |

---

## 4. Deliverable Solution Artifact Inventory

| Run | Exported Artifact | Profile | Code Lines | Diff Lines | SHA-256 Digest | Status |
|:---:|---|:---:|:---:|:---:|:---:|:---:|
| run-01 | `task-3-test-config-a-r1.py` | **A** | 198 | 192 | `7fea01b11d09e3ea...` | **PASS** |
| run-02 | `task-3-test-config-u1-r1.py` | **U1** | 142 | 140 | `cb52b866f3918e98...` | **PASS** |
| run-03 | `task-3-test-config-c-r1.py` | **C** | 144 | 130 | `426fb0e9ab2f17c0...` | **PASS** |
| run-04 | `task-3-test-config-u2-r1.py` | **U2** | 199 | 175 | `8ea7939a39157958...` | **PASS** |
| run-05 | `task-3-test-config-u3-r1.py` | **U3** | 154 | 184 | `af5ab95a6447e038...` | **PASS** |
| run-06 | `task-3-test-config-u4-r1.py` | **U4** | 156 | 158 | `903e1cd5e1e3e5e7...` | **PASS** |
| run-07 | `task-4-test-config-u4-r1.py` | **U4** | 92 | 93 | `c1ecb420b35813e0...` | **PASS** |
| run-08 | `task-4-test-config-u3-r1.py` | **U3** | 99 | 144 | `364d60dbe0551195...` | **PASS** |
| run-09 | `task-4-test-config-u2-r1.py` | **U2** | 116 | 111 | `a6e3a83787c88f0d...` | **PASS** |
| run-10 | `task-4-test-config-c-r1.py` | **C** | 106 | 117 | `00d8a9a950b17739...` | **PASS** |
| run-11 | `task-4-test-config-u1-r1.py` | **U1** | 102 | 113 | `0274563fc22e1f63...` | **PASS** |
| run-12 | `task-4-test-config-a-r1.py` | **A** | 125 | 134 | `43dd7f98cbb25f09...` | **PASS** |
| run-13 | `task-3-test-config-u2-r2.py` | **U2** | 202 | 140 | `07db3164b94cf0e5...` | **PASS** |
| run-14 | `task-3-test-config-u3-r2.py` | **U3** | 160 | 180 | `fb89e7c1b1d7d12f...` | **PASS** |
| run-15 | `task-3-test-config-u4-r2.py` | **U4** | 166 | 194 | `c04f2bde7e78d1c7...` | **PASS** |
| run-16 | `task-3-test-config-a-r2.py` | **A** | 215 | 261 | `fb3fbf4461895e7a...` | **PASS** |
| run-17 | `task-3-test-config-u1-r2.py` | **U1** | 142 | 120 | `e573dd0614263639...` | **PASS** |
| run-18 | `task-3-test-config-c-r2.py` | **C** | 166 | 150 | `f510d2bec5de1846...` | **PASS** |
| run-19 | `task-4-test-config-c-r2.py` | **C** | 116 | 127 | `a19db59abb64d7f5...` | **PASS** |
| run-20 | `task-4-test-config-u1-r2.py` | **U1** | 84 | 97 | `7c2df20431a62a31...` | **PASS** |
| run-21 | `task-4-test-config-a-r2.py` | **A** | 120 | 129 | `6cb96d837c9fd3f6...` | **PASS** |
| run-22 | `task-4-test-config-u4-r2.py` | **U4** | 97 | 102 | `23b03003a617a1ad...` | **PASS** |
| run-23 | `task-4-test-config-u3-r2.py` | **U3** | 110 | 121 | `f188063ebc5d1a2f...` | **PASS** |
| run-24 | `task-4-test-config-u2-r2.py` | **U2** | 111 | 126 | `dc3c6682070d4566...` | **PASS** |
| run-25 | `task-3-test-config-u3-r3.py` | **U3** | 155 | 183 | `e4399cfd826358a3...` | **PASS** |
| run-26 | `task-3-test-config-u4-r3.py` | **U4** | 158 | 136 | `7ebef6691ce81ade...` | **PASS** |
| run-27 | `task-3-test-config-a-r3.py` | **A** | 185 | 219 | `a0797fbf3e47f357...` | **PASS** |
| run-28 | `task-3-test-config-u1-r3.py` | **U1** | 147 | 153 | `975d7a84311e83f6...` | **PASS** |
| run-29 | `task-3-test-config-c-r3.py` | **C** | 166 | 150 | `fa5c73c6458714e4...` | **PASS** |
| run-30 | `task-3-test-config-u2-r3.py` | **U2** | 196 | 200 | `5ff8d57e67d12f5f...` | **PASS** |
| run-31 | `task-4-test-config-u1-r3.py` | **U1** | 92 | 95 | `67bbe3a494accc48...` | **PASS** |
| run-32 | `task-4-test-config-c-r3.py` | **C** | 98 | 107 | `3e6b6aae0cb3b430...` | **PASS** |
| run-33 | `task-4-test-config-u2-r3.py` | **U2** | 124 | 123 | `fcac05f3013ed78e...` | **PASS** |
| run-34 | `task-4-test-config-u3-r3.py` | **U3** | 104 | 117 | `bad0c59d406becee...` | **PASS** |
| run-35 | `task-4-test-config-u4-r3.py` | **U4** | 98 | 109 | `12d242752827b59a...` | **PASS** |
| run-36 | `task-4-test-config-a-r3.py` | **A** | 121 | 146 | `81d63909c5996086...` | **PASS** |

---

## 5. Token Economics & Output Decomposition

In autonomous coding benchmarks, total cost and tokens break down into four distinct categories:
1. **Natural Language Output Tokens:** Conversational prose, explanations, greetings, and recaps. Caveman Ultra aggressively targets this component, stripping grammatical fluff down to payload-bearing fragments.
2. **Model Reasoning & Output Tokens:** GPT-6 Luna Max reasoning tokens and code generations. While prompt directives change response terseness, internal thinking tokens for complex SQLite transactions or symlink edge-case analysis remain largely invariant.
3. **Tool-Result Tokens:** Context input tokens from filesystem reads and command outputs. Profile U4 (RTK) intercepts Bash commands to compress tool outputs before they enter the model context.
4. **Prompt-Cache Dynamics:** OpenAI Codex prompt caching charges significantly less for cache hits ($0.02/M) than fresh input writes ($0.20/M). Changing system prompt instructions alters the cache prefix. Stable prefixes yield higher cache hit rates.

---

## 6. Correctness, Concurrency & Failure Analysis

### Evaluated Challenges
- **Task 3 (Concurrent Inventory Reservation):** Evaluated against 18 hidden test assertions covering SQLite `BEGIN IMMEDIATE` transaction locking, idempotency across concurrent threads, rollback safety on operational failure, and preventing inventory overselling.
- **Task 4 (Crash-Safe Incremental File Indexer):** Evaluated against 18 hidden checks including circular symlink loops, streaming SHA-256 computation in 64 KB buffers, atomic temporary-file replacement, and refusal to overwrite corrupted manifests.

### Optimizer Pathology Analysis
- **Ponytail Omissions:** Monitored whether aggressive YAGNI directives caused workers to skip necessary error handling, transactions, or validation.
- **Caveman Ultra Truncations:** Monitored whether extreme fragment terseness caused syntax errors, truncated code blocks, or broken API identifiers.

---

## 7. RTK Interception & Compression Performance

- **Commands Intercepted (Profile U4):** 24
- **Raw Bash Tool Output:** 2,153 bytes
- **Compressed Tool Output:** 1,899 bytes
- **Effective Compression Ratio:** 11.8%
- **Estimated Tokens Saved:** 254
- **Total Compression Overhead:** 48 ms
- **Recall / Elision Efficiency:** Verified SQLite recall mode preserved failure diagnostics while intercepting verbose directory trees and search outputs.

---

## 8. Headroom Profile U5 Feasibility & Compatibility Audit

**Status:** **UNSUPPORTED**  
- **Audit Finding:** Headroom 0.41.0 reverse proxy is designed for standard Anthropic (`/v1/messages`) and OpenAI (`/v1`) REST endpoints. It does not provide native support for Pi's `openai-codex` provider transport, which connects to `https://chatgpt.com/backend-api` using the `openai-codex-responses` SSE streaming protocol.
- **Authentication Barrier:** Pi authenticates to `openai-codex` via internal OAuth session tokens stored in its secure credential store. Headroom does not support this proprietary authentication flow without MITM token extraction or unauthorized model substitution, both of which violate benchmark isolation and safety constraints.
- **Conclusion:** Profile U5 was excluded from model execution in strict accordance with the benchmark specification, preserving benchmark harness integrity.

---

## 9. Evaluator Isolation & Anti-Leakage Audit

- **Attempted Access to Hidden Evaluators:** 0 detected across all runs.
- **Filesystem Sandboxing:** During each worker run, `BenchmarkFilesystemIsolation` locked permissions (`chmod 000`) on evaluator files (`round-2/evaluators/eval_task_3.py`, `eval_task_4.py`), previous round results, and sibling workspaces. Permissions were safely restored after subprocess exit.
- **Transcript Verification:** Continuous parsing of tool execution events confirmed zero attempts by workers to inspect hidden tests, benchmark results, or external reference implementations.

---

## 10. Final Recommendation

### Priority Ranking Criteria
1. **Correctness & Reliability** (Must maintain ~100% pass rate)
2. **Cost per Successful Task** (Total cost divided by verified passes)
3. **Total Completion Time** (Developer latency)
4. **Token Consumption** (Input, output, and cache efficiency)
5. **Implementation Simplicity** (Minimal diff and clean code)

### Official Determination
- **Top Reliability Profile:** `A` (100.0%)
- **Lowest Cost per Verified Task:** `A` ($0.007951)

