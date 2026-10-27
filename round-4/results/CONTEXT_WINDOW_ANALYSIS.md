# Round 4 Context Window Consumption Analysis

## Methodology
Per Section 10 of the benchmark specification, active context size per model request is defined as:
```text
request_input_context = input + cacheRead + cacheWrite
```
For every completed assistant model response, the raw `message_end` event in Pi's JSONL telemetry is parsed.
Context utilization percentage is computed relative to the verified model context window limit: **272,000 tokens** (`openai-codex/gpt-6-luna`).

## Active Context Statistics by Profile

| Profile | Runs | Mean Peak Context | Median Peak Context | Mean Median Context | Cumulative Context (Mean) | Mean Cache Read Rate | Peak Context % Limit |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **A — Baseline** | 9 | 26,699 | 28,966 | 18,905 | 195,131 | 77.8% | 9.82% |
| **C — Combined Lite** | 9 | 21,592 | 21,412 | 16,938 | 142,018 | 73.3% | 7.94% |
| **U3 — Combined Ultra** | 9 | 23,107 | 23,622 | 17,471 | 167,019 | 78.8% | 8.49% |

## Per-Run Context Trajectory Breakdown

| Run ID | Profile | Task | Requests | First Req Context | Peak Req Context | Median Req Context | Final Req Context | Compaction Preceded Peak |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `run-01` | **A** | `task-5` | 13 | 3,366 | 34,186 | 21,850.0 | 34,186 | No |
| `run-02` | **C** | `task-6` | 6 | 3,933 | 14,650 | 10,407.0 | 14,650 | No |
| `run-03` | **U3** | `task-7` | 10 | 3,848 | 22,123 | 19,163.0 | 22,123 | No |
| `run-04` | **C** | `task-5` | 15 | 3,746 | 28,702 | 24,762.0 | 28,702 | No |
| `run-05` | **U3** | `task-6` | 7 | 4,077 | 14,880 | 12,392.0 | 14,880 | No |
| `run-06` | **A** | `task-7` | 9 | 3,324 | 28,966 | 19,561.0 | 28,966 | No |
| `run-07` | **U3** | `task-5` | 9 | 3,890 | 23,622 | 20,088.0 | 23,622 | No |
| `run-08` | **A** | `task-6` | 8 | 3,553 | 18,286 | 13,761.5 | 18,286 | No |
| `run-09` | **C** | `task-7` | 7 | 3,704 | 21,412 | 18,409.0 | 21,412 | No |
| `run-10` | **C** | `task-5` | 8 | 3,746 | 23,484 | 17,472.0 | 23,484 | No |
| `run-11` | **U3** | `task-6` | 8 | 4,077 | 16,595 | 13,136.0 | 16,595 | No |
| `run-12` | **A** | `task-7` | 16 | 3,324 | 32,357 | 24,519.5 | 32,357 | No |
| `run-13` | **U3** | `task-5` | 13 | 3,890 | 28,833 | 18,086.0 | 28,833 | No |
| `run-14` | **A** | `task-6` | 9 | 3,553 | 16,168 | 12,438.0 | 16,168 | No |
| `run-15` | **C** | `task-7` | 8 | 3,704 | 21,318 | 17,814.0 | 21,318 | No |
| `run-16` | **A** | `task-5` | 9 | 3,366 | 27,606 | 18,121.0 | 27,606 | No |
| `run-17` | **C** | `task-6` | 9 | 3,933 | 19,444 | 12,636.0 | 19,444 | No |
| `run-18` | **U3** | `task-7` | 9 | 3,848 | 25,092 | 18,537.0 | 25,092 | No |
| `run-19` | **U3** | `task-5` | 16 | 3,890 | 27,941 | 21,336.0 | 27,941 | No |
| `run-20` | **A** | `task-6` | 8 | 3,553 | 16,748 | 13,464.5 | 16,748 | No |
| `run-21` | **C** | `task-7` | 9 | 3,704 | 22,268 | 17,586.0 | 22,268 | No |
| `run-22` | **A** | `task-5` | 9 | 3,366 | 32,257 | 20,933.0 | 32,257 | No |
| `run-23` | **C** | `task-6` | 11 | 3,933 | 14,428 | 11,220.0 | 14,428 | No |
| `run-24` | **U3** | `task-7` | 9 | 3,848 | 28,337 | 19,881.0 | 28,337 | No |
| `run-25` | **C** | `task-5` | 11 | 3,746 | 28,620 | 22,138.0 | 28,620 | No |
| `run-26` | **U3** | `task-6` | 13 | 4,077 | 20,538 | 14,621.0 | 20,538 | No |
| `run-27` | **A** | `task-7` | 16 | 3,324 | 33,720 | 25,493.0 | 33,720 | No |

## Findings on Context Growth and Cache Retention
1. **Prompt Overlay Impact:** Profile A (no prompt overlay) starts with lower initial turn tokens than C and U3, but subsequent tool outputs dominate context growth.
2. **Cache Read Efficiency:** Cache read rate remained high across repeated turns within the same session.
3. **Context Ceiling Headroom:** Peak reported input contexts remained comfortably below the 272K model limit.

