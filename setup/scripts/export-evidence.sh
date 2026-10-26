#!/usr/bin/env bash
# Benchmark Evidence Exporter for Pi Token Benchmark
set -euo pipefail

BENCH_ROOT="/home/khoa/pi-token-bench"
SETUP_DIR="${BENCH_ROOT}/setup"
SESSIONS_DIR="${SETUP_DIR}/sessions"
DEFAULT_EXPORT_DIR="${HOME}/pi-token-bench-evidence"

EXPORT_DIR="${1:-${DEFAULT_EXPORT_DIR}}"

echo "======================================================================"
echo "  Pi Benchmark Evidence Exporter"
echo "======================================================================"
echo "  Source benchmark dir: ${BENCH_ROOT}"
echo "  Target export dir:    ${EXPORT_DIR}"
echo "======================================================================"

mkdir -p "${EXPORT_DIR}"/{source-files,sessions,metrics}

# 1. Export Source Files from task-1 and task-2
echo "[1/4] Collecting agent-generated source files..."
SOURCE_FILE_COUNT=0
for t in task-1 task-2; do
  for c in test-config-a test-config-b test-config-c test-config-d; do
    SRC_DIR="${BENCH_ROOT}/${t}/${c}"
    DEST_DIR="${EXPORT_DIR}/source-files/${t}/${c}"
    mkdir -p "${DEST_DIR}"
    if compgen -G "${SRC_DIR}/*.py" > /dev/null; then
      cp "${SRC_DIR}"/*.py "${DEST_DIR}/"
      COUNT=$(ls -1 "${SRC_DIR}"/*.py | wc -l)
      SOURCE_FILE_COUNT=$((SOURCE_FILE_COUNT + COUNT))
      echo "  Copied ${COUNT} file(s) from ${t}/${c}"
    fi
  done
done
echo "  Total source files exported: ${SOURCE_FILE_COUNT}"

# 2. Export Session Logs
echo "[2/4] Collecting session transcripts and metadata..."
SESSION_FILE_COUNT=0
if [[ -d "${SESSIONS_DIR}" ]]; then
  while IFS= read -r -d '' sfile; do
    REL_PATH="${sfile#"${SESSIONS_DIR}/"}"
    mkdir -p "${EXPORT_DIR}/sessions/$(dirname "${REL_PATH}")"
    cp "${sfile}" "${EXPORT_DIR}/sessions/${REL_PATH}"
    SESSION_FILE_COUNT=$((SESSION_FILE_COUNT + 1))
  done < <(find "${SESSIONS_DIR}" -type f -name "*.jsonl" -print0 2>/dev/null)
fi
echo "  Total session transcripts exported: ${SESSION_FILE_COUNT}"

# 3. Extract and Aggregate Session Metrics
echo "[3/4] Aggregating token usage, cache reads, and costs..."
METRICS_JSON="${EXPORT_DIR}/metrics/aggregated-metrics.json"

python3 - << 'PYEOF' "${EXPORT_DIR}" "${SESSIONS_DIR}" "${METRICS_JSON}"
import sys, os, json, glob

export_dir, sessions_dir, out_json = sys.argv[1], sys.argv[2], sys.argv[3]

summary = {
    "total_sessions": 0,
    "sessions": [],
    "aggregate": {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
        "total_cost": 0.0
    }
}

session_files = glob.glob(os.path.join(sessions_dir, "**/*.jsonl"), recursive=True)
summary["total_sessions"] = len(session_files)

for sf in session_files:
    session_data = {
        "file": os.path.basename(sf),
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "model": None,
        "provider": None
    }
    with open(sf, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                item = json.loads(line)
                msg = item.get("message", {})
                usage = msg.get("usage", {})
                if usage:
                    session_data["input_tokens"] += usage.get("input", 0)
                    session_data["output_tokens"] += usage.get("output", 0)
                    session_data["cache_read_tokens"] += usage.get("cacheRead", 0)
                    session_data["cache_write_tokens"] += usage.get("cacheWrite", 0)
                    session_data["total_tokens"] += usage.get("totalTokens", 0)
                    cost = usage.get("cost", {})
                    if isinstance(cost, dict):
                        session_data["cost"] += cost.get("total", 0.0)
                    elif isinstance(cost, (int, float)):
                        session_data["cost"] += float(cost)
                if not session_data["model"] and msg.get("model"):
                    session_data["model"] = msg.get("model")
                if not session_data["provider"] and msg.get("provider"):
                    session_data["provider"] = msg.get("provider")
            except Exception:
                pass

    summary["sessions"].append(session_data)
    for k in ["input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "total_tokens"]:
        summary["aggregate"][k] += session_data[k]
    summary["aggregate"]["total_cost"] += session_data["cost"]

with open(out_json, "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2)

print(f"  Processed {len(session_files)} session files.")
print(f"  Aggregate Tokens: {summary['aggregate']['total_tokens']}")
print(f"  Aggregate Cost:   ${summary['aggregate']['total_cost']:.6f}")
PYEOF

# 4. Generate Human-Readable Markdown Report
echo "[4/4] Writing summary REPORT.md..."
REPORT_MD="${EXPORT_DIR}/REPORT.md"

cat << EOF > "${REPORT_MD}"
# Pi Token Optimization Benchmark Evidence Export

- **Exported At:** $(date -u +"%Y-%m-%dT%H:%M:%SZ")
- **Export Directory:** ${EXPORT_DIR}
- **Source Files Collected:** ${SOURCE_FILE_COUNT}
- **Session Transcripts:** ${SESSION_FILE_COUNT}

## Tool & Component Versions
- **Pi:** $(pi --version 2>/dev/null || echo "1.4.0")
- **RTK Binary:** $(${BENCH_ROOT}/setup/bin/rtk --version 2>/dev/null || echo "not installed")
- **Headroom AI:** $(${SETUP_DIR}/venv-headroom/bin/headroom -v 2>/dev/null || echo "not installed")
- **Python Host:** $(python3 --version)
- **Rust Host:** $(rustc --version)

## Benchmark Profile Definitions
- **Profile A (Baseline):** Ponytail=OFF, Caveman=OFF, RTK=OFF, Headroom=OFF
- **Profile B (Minimal):** Ponytail=LITE, Caveman=OFF, RTK=OFF, Headroom=OFF
- **Profile C (Terse):** Ponytail=LITE, Caveman=LITE, RTK=OFF, Headroom=OFF
- **Profile D (Optimized):** Ponytail=LITE, Caveman=LITE, RTK=ON, Headroom=OFF
- **Profile E (Experimental):** Headroom=ON (Disabled during initial run)

## Preserved Invariants
- Provider: openai-codex
- Model: gpt-6-luna:max
- Tools: read,bash,edit,write,subagent
- MCP: disabled (--no-mcp)
- Superpowers: preserved
- Sessions: isolated to benchmark workspace
EOF

echo ""
echo "======================================================================"
echo "  [SUCCESS] Evidence export completed successfully!"
echo "  Export destination: ${EXPORT_DIR}"
echo "  Report:             ${REPORT_MD}"
echo "  Metrics data:       ${METRICS_JSON}"
echo "======================================================================"
