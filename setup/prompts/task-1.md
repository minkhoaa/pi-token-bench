# Task 1: Streaming HTTP Log Analyzer

Create exactly one Python source file in the current working directory.

## File Naming
The file MUST be named:
`task-1-<current-folder-name>.py`
For example, if the current directory is `test-config-a`, name the file `task-1-test-config-a.py`. Derive the folder name from the current working directory.

## Implementation Specification
Implement a function:
```python
from typing import Iterable

def analyze_logs(lines: Iterable[str]) -> dict:
    ...
```

### Requirements:
1. **Input Format:**
   - Input `lines` is an iterable of strings (JSONL), one record per line.
   - Ignore blank and whitespace-only lines.
   - Each valid record is a JSON object with:
     - `route`: nonempty trimmed string.
     - `status`: integer between 100 and 599 inclusive. Booleans (`True`/`False`) MUST NOT be accepted as integers.
     - `duration_ms`: finite, nonnegative int or float (`duration_ms >= 0`, not NaN/Inf). Booleans MUST NOT be accepted as numbers.
2. **Invalid Record Handling:**
   - Any line with malformed JSON, missing fields, invalid types, or out-of-range values is invalid.
   - Count invalid records in `total_invalid` without crashing or raising exceptions.
   - Count valid records in `total_valid`.
3. **Route Aggregation:**
   - Group valid records by trimmed route name (e.g. `" /api/users "` is grouped under `"/api/users"`).
   - For each route, compute:
     - `count`: total valid requests for this route.
     - `errors`: number of requests with HTTP status in the 5xx range (500 <= status <= 599).
     - `avg_ms`: average `duration_ms`, rounded to 2 decimal places.
     - `p95_ms`: nearest-rank 95th percentile duration, rounded to 2 decimal places.
       Nearest-rank formula: for $N$ sorted durations, the 0-based index is `math.ceil(0.95 * N) - 1`.
4. **Ordering:**
   - Sort `routes` by request `count` descending. Break ties by `route` name ascending (lexicographical).
5. **Streaming / Memory:**
   - Process lines incrementally. Storing durations per route to compute exact P95 is permitted.
6. **Return Format:**
   Return a dictionary matching this structure exactly:
```json
{
  "total_valid": 3,
  "total_invalid": 1,
  "routes": [
    {
      "route": "/api/users",
      "count": 3,
      "errors": 1,
      "avg_ms": 120.0,
      "p95_ms": 200.0
    }
  ]
}
```
   If there are no valid records, `routes` is an empty list `[]`.

## CLI Interface
Support running the script from command line:
`python <script-name> INPUT.jsonl`
- On success: Print the resulting JSON to stdout with exit code 0.
- On unreadable input file (non-existent file, permission error, directory): Print a readable error message to stderr without an unhandled traceback and exit with a nonzero status code.

Support running embedded unit tests:
`python <script-name> --self-test`
- Run `unittest.main(argv=[sys.argv[0]])` or equivalent test runner.
- Include at least eight focused unittest cases in the same source file, covering:
  1. Aggregation (total counts and averages)
  2. HTTP 5xx error counting
  3. Nearest-rank P95 duration
  4. Malformed JSON lines
  5. Field validation (route string, status range/booleans, duration_ms validity/booleans)
  6. Blank lines handling
  7. Route sorting (count descending, name ascending)
  8. Empty input handling (0 valid, 0 invalid, empty routes)
  9. CLI failure on unreadable input file

## Constraints
- Python 3.11+ standard library only (`json`, `sys`, `math`, `unittest`, `typing`, `pathlib`, etc.).
- No third-party packages or pip installs.
- No network calls.
- Exactly ONE `.py` source file in the current working directory. No extra files or directories.
- Do not compromise validation or tests to shorten code.

## Verification & Response
1. Run self-tests with `--self-test`.
2. Run a CLI smoke test on sample data.
3. Keep the final response concise: report filename, test results, implementation summary, and any errors/retries.
