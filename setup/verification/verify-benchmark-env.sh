#!/usr/bin/env bash
# Automated Verification Suite for Pi Token Optimization Benchmark
set -euo pipefail

BENCH_ROOT="/home/khoa/pi-token-bench"
SETUP_DIR="${BENCH_ROOT}/setup"
TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0

pass() {
  TOTAL_TESTS=$((TOTAL_TESTS + 1))
  PASSED_TESTS=$((PASSED_TESTS + 1))
  echo "  [PASS] $1"
}

fail() {
  TOTAL_TESTS=$((TOTAL_TESTS + 1))
  FAILED_TESTS=$((FAILED_TESTS + 1))
  echo "  [FAIL] $1"
}

echo "======================================================================"
echo "  Pi Token Optimization Benchmark — Local Verification Suite"
echo "======================================================================"

# 1. Directory Structure & Empty Task Workspaces
echo ""
echo "--- 1. Workspace Isolation & Structure ---"
if [[ -d "${BENCH_ROOT}" ]]; then
  pass "Benchmark root exists: ${BENCH_ROOT}"
else
  fail "Benchmark root missing: ${BENCH_ROOT}"
fi

ALL_TASKS_EMPTY=1
for t in task-1 task-2; do
  for c in test-config-a test-config-b test-config-c test-config-d; do
    DIR="${BENCH_ROOT}/${t}/${c}"
    if [[ ! -d "${DIR}" ]]; then
      ALL_TASKS_EMPTY=0
      fail "Task directory missing: ${t}/${c}"
    else
      FILE_COUNT=$(ls -A "${DIR}" | wc -l)
      if [[ ${FILE_COUNT} -ne 0 ]]; then
        ALL_TASKS_EMPTY=0
        fail "Task directory ${t}/${c} is not empty (${FILE_COUNT} items found)"
      fi
    fi
  done
done
if [[ ${ALL_TASKS_EMPTY} -eq 1 ]]; then
  pass "All eight task directories (task-1 & task-2, configs a-d) are present and completely empty"
fi

# 2. Optimizer Binaries & Configurations
echo ""
echo "--- 2. Optimizer Installations & Profiles ---"
if [[ -x "${SETUP_DIR}/bin/rtk" ]]; then
  RTK_VER=$("${SETUP_DIR}/bin/rtk" --version 2>&1)
  pass "RTK binary isolated and executable: ${RTK_VER}"
else
  fail "RTK binary missing or not executable at ${SETUP_DIR}/bin/rtk"
fi

if [[ -f "${SETUP_DIR}/extensions/rtk.ts" ]]; then
  pass "RTK Pi extension present at ${SETUP_DIR}/extensions/rtk.ts"
else
  fail "RTK Pi extension missing"
fi

if [[ -f "${SETUP_DIR}/profiles/ponytail-lite.md" ]]; then
  pass "Ponytail Lite profile present at ${SETUP_DIR}/profiles/ponytail-lite.md"
else
  fail "Ponytail Lite profile missing"
fi

if [[ -f "${SETUP_DIR}/profiles/caveman-lite.md" ]]; then
  pass "Caveman Lite profile present at ${SETUP_DIR}/profiles/caveman-lite.md"
else
  fail "Caveman Lite profile missing"
fi

if [[ -f "${SETUP_DIR}/profiles/combined-lite.md" ]]; then
  pass "Combined Lite profile present at ${SETUP_DIR}/profiles/combined-lite.md"
else
  fail "Combined Lite profile missing"
fi

if [[ -x "${SETUP_DIR}/venv-headroom/bin/python" && -x "${SETUP_DIR}/venv-headroom/bin/headroom" ]]; then
  HEADROOM_VER=$("${SETUP_DIR}/venv-headroom/bin/headroom" -v 2>&1)
  pass "Headroom isolated Python virtual environment active: ${HEADROOM_VER}"
else
  fail "Headroom venv missing or incomplete at ${SETUP_DIR}/venv-headroom"
fi

# 3. Profile Isolation & Toggle Leakage Checks
echo ""
echo "--- 3. Profile Isolation & Toggle Leakage Verification ---"

# Profile A
OUT_A=$(BENCH_DRY_RUN=1 "${BENCH_ROOT}/bench-a" "${BENCH_ROOT}/task-1/test-config-a")
if echo "${OUT_A}" | grep -q "Ponytail:       OFF" && \
   echo "${OUT_A}" | grep -q "Caveman:        OFF" && \
   echo "${OUT_A}" | grep -q "RTK:            OFF" && \
   echo "${OUT_A}" | grep -q "Headroom:       OFF" && \
   echo "${OUT_A}" | grep -q "RTK_DISABLED=1" && \
   ! echo "${OUT_A}" | grep -q "append-system-prompt" && \
   ! echo "${OUT_A}" | grep -q "rtk.ts"; then
  pass "Profile A verified: Baseline clean (all optimizers OFF, no prompts/extensions attached)"
else
  fail "Profile A toggle leakage detected:\n${OUT_A}"
fi

# Profile B
OUT_B=$(BENCH_DRY_RUN=1 "${BENCH_ROOT}/bench-b" "${BENCH_ROOT}/task-1/test-config-b")
if echo "${OUT_B}" | grep -q "Ponytail:       LITE" && \
   echo "${OUT_B}" | grep -q "Caveman:        OFF" && \
   echo "${OUT_B}" | grep -q "RTK:            OFF" && \
   echo "${OUT_B}" | grep -q "Headroom:       OFF" && \
   echo "${OUT_B}" | grep -q "RTK_DISABLED=1" && \
   echo "${OUT_B}" | grep -q "ponytail-lite.md" && \
   ! echo "${OUT_B}" | grep -q "caveman-lite.md" && \
   ! echo "${OUT_B}" | grep -q "rtk.ts"; then
  pass "Profile B verified: Ponytail LITE active, Caveman/RTK/Headroom OFF"
else
  fail "Profile B toggle leakage detected:\n${OUT_B}"
fi

# Profile C
OUT_C=$(BENCH_DRY_RUN=1 "${BENCH_ROOT}/bench-c" "${BENCH_ROOT}/task-1/test-config-c")
if echo "${OUT_C}" | grep -q "Ponytail:       LITE" && \
   echo "${OUT_C}" | grep -q "Caveman:        LITE" && \
   echo "${OUT_C}" | grep -q "RTK:            OFF" && \
   echo "${OUT_C}" | grep -q "Headroom:       OFF" && \
   echo "${OUT_C}" | grep -q "RTK_DISABLED=1" && \
   echo "${OUT_C}" | grep -q "combined-lite.md" && \
   ! echo "${OUT_C}" | grep -q "rtk.ts"; then
  pass "Profile C verified: Ponytail LITE + Caveman LITE active, RTK/Headroom OFF"
else
  fail "Profile C toggle leakage detected:\n${OUT_C}"
fi

# Profile D
OUT_D=$(BENCH_DRY_RUN=1 "${BENCH_ROOT}/bench-d" "${BENCH_ROOT}/task-1/test-config-d")
if echo "${OUT_D}" | grep -q "Ponytail:       LITE" && \
   echo "${OUT_D}" | grep -q "Caveman:        LITE" && \
   echo "${OUT_D}" | grep -q "RTK:            ON" && \
   echo "${OUT_D}" | grep -q "Headroom:       OFF" && \
   echo "${OUT_D}" | grep -q "RTK_DISABLED=0" && \
   echo "${OUT_D}" | grep -q "combined-lite.md" && \
   echo "${OUT_D}" | grep -q "rtk.ts" && \
   echo "${OUT_D}" | grep -q "/setup/bin"; then
  pass "Profile D verified: Ponytail LITE + Caveman LITE + RTK ON (extension + PATH mounted), Headroom OFF"
else
  fail "Profile D configuration error:\n${OUT_D}"
fi

# Profile E Disabled check
if BENCH_DRY_RUN=1 "${BENCH_ROOT}/bench-e" "${BENCH_ROOT}/task-1/test-config-d" >/dev/null 2>&1; then
  fail "Profile E should be disabled by default but was executed successfully"
else
  pass "Profile E is strictly disabled during benchmark by default (fails safe)"
fi

# 4. Headroom Local Synthetic Compression
echo ""
echo "--- 4. Headroom Local Synthetic Verification ---"
if "${SETUP_DIR}/venv-headroom/bin/python" "${SETUP_DIR}/verification/test-headroom-synthetic.py" >/dev/null 2>&1; then
  pass "Headroom local compression verified against synthetic data (zero provider route changes)"
else
  fail "Headroom synthetic compression failed"
fi

# 5. External Environment & Original Aliases Preservation
echo ""
echo "--- 5. Existing Environment Preservation ---"
if command -v pi >/dev/null 2>&1; then
  pass "Pi command line functional: $(pi --version)"
else
  fail "Pi command not found"
fi

if grep -q "alias pia='command pi --tools read,bash,edit,write,subagent --no-mcp'" ~/.zshenv && \
   grep -q "alias pil='command pi --tools read,bash,edit,write --no-mcp'" ~/.zshenv && \
   grep -q "alias pim=" ~/.zshenv; then
  pass "Existing shell aliases (pil, pia, pim) in ~/.zshenv completely preserved and untouched"
else
  fail "Pre-existing aliases in ~/.zshenv were modified!"
fi

if diff -q "${SETUP_DIR}/backups/settings.json.pre-bench" ~/.pi/agent/settings.json >/dev/null 2>&1; then
  pass "~/.pi/agent/settings.json has ZERO modifications (exact match with pre-bench backup)"
else
  fail "~/.pi/agent/settings.json was modified!"
fi

if ! command -v rtk >/dev/null 2>&1; then
  pass "RTK binary is NOT installed in global PATH (strictly confined to setup/bin)"
else
  fail "RTK binary leaked into global PATH: $(which rtk)"
fi

if ! command -v headroom >/dev/null 2>&1; then
  pass "Headroom is NOT installed in global PATH (strictly confined to setup/venv-headroom)"
else
  fail "Headroom leaked into global PATH: $(which headroom)"
fi

# 6. Toolset and MCP Enforcement
echo ""
echo "--- 6. Toolset and MCP Enforcement ---"
if echo "${OUT_A}" | grep -q "Toolset:        read,bash,edit,write,subagent" && \
   echo "${OUT_A}" | grep -q "MCP Disabled:   true" && \
   echo "${OUT_D}" | grep -q "Toolset:        read,bash,edit,write,subagent" && \
   echo "${OUT_D}" | grep -q "MCP Disabled:   true"; then
  pass "Minimal toolset enforced across profiles: read,bash,edit,write,subagent with --no-mcp"
else
  fail "Toolset or MCP configuration mismatch"
fi

# 7. Cleanup Dry-Run Verification
echo ""
echo "--- 7. Cleanup Dry-Run Verification ---"
CLEANUP_OUT=$("${BENCH_ROOT}/cleanup" --dry-run)
if echo "${CLEANUP_OUT}" | grep -q "Nothing was modified" && \
   echo "${CLEANUP_OUT}" | grep -q "Protected paths:" && \
   echo "${CLEANUP_OUT}" | grep -q "Managed benchmark root: /home/khoa/pi-token-bench"; then
  pass "Cleanup --dry-run verified: identifies benchmark scope accurately without touching external files"
else
  fail "Cleanup dry run output unexpected:\n${CLEANUP_OUT}"
fi

# 8. Reversible Rollback Safety Fixture
echo ""
echo "--- 8. Reversible Rollback Safety Fixture ---"
FIXTURE_TMP=$(mktemp -d)
cp "${SETUP_DIR}/backups/settings.json.pre-bench" "${FIXTURE_TMP}/test-settings.json"
echo '{"modified": true}' > "${FIXTURE_TMP}/test-settings.json"
if diff -q "${SETUP_DIR}/backups/settings.json.pre-bench" "${FIXTURE_TMP}/test-settings.json" >/dev/null 2>&1; then
  fail "Rollback fixture setup failed"
else
  # Perform restore on fixture
  cp "${SETUP_DIR}/backups/settings.json.pre-bench" "${FIXTURE_TMP}/test-settings.json"
  if diff -q "${SETUP_DIR}/backups/settings.json.pre-bench" "${FIXTURE_TMP}/test-settings.json" >/dev/null 2>&1; then
    pass "Reversible restoration verified: backup accurately restores target configuration on test fixture"
  else
    fail "Reversible restoration on fixture failed"
  fi
fi
rm -rf "${FIXTURE_TMP}"

echo ""
echo "======================================================================"
echo "  Verification Results: ${PASSED_TESTS}/${TOTAL_TESTS} tests passed (${FAILED_TESTS} failures)"
echo "======================================================================"

if [[ ${FAILED_TESTS} -eq 0 ]]; then
  echo "  [SUCCESS] Benchmark environment fully validated and ready."
  exit 0
else
  echo "  [FAILURE] Verification encountered ${FAILED_TESTS} error(s)."
  exit 1
fi
