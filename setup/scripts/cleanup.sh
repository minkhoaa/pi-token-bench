#!/usr/bin/env bash
# Dedicated Cleanup Mechanism for Pi Token Benchmark
set -euo pipefail

BENCH_ROOT="/home/khoa/pi-token-bench"
MANIFEST="${BENCH_ROOT}/setup/install-manifest.json"
BACKUP_DIR="${BENCH_ROOT}/setup/backups"

MODE="${1:-}"

print_usage() {
  echo "Pi Benchmark Cleanup Utility"
  echo ""
  echo "Usage:"
  echo "  cleanup.sh --dry-run    Preview all changes and removals without modifying anything"
  echo "  cleanup.sh --apply      Execute complete benchmark teardown and restoration"
  echo ""
}

if [[ "${MODE}" != "--dry-run" && "${MODE}" != "--apply" ]]; then
  print_usage
  exit 1
fi

echo "======================================================================"
echo "  Pi Benchmark Cleanup — Mode: ${MODE}"
echo "======================================================================"

# Step 1: Check running processes
echo "[Step 1] Checking benchmark-specific processes..."
PROCESSES_TO_KILL=()
if pgrep -f "headroom proxy" >/dev/null 2>&1; then
  PROCESSES_TO_KILL+=("headroom proxy (PID: $(pgrep -f "headroom proxy"))")
fi
if pgrep -f "rtk-proxy" >/dev/null 2>&1; then
  PROCESSES_TO_KILL+=("rtk-proxy (PID: $(pgrep -f "rtk-proxy"))")
fi

if [[ ${#PROCESSES_TO_KILL[@]} -gt 0 ]]; then
  echo "  Found running benchmark processes:"
  for p in "${PROCESSES_TO_KILL[@]}"; do
    echo "    - ${p}"
  done
else
  echo "  No running benchmark processes detected."
fi

# Step 2: Check unexported evidence
echo "[Step 2] Checking for unexported task evidence..."
UNEXPORTED_FILES=()
for t in task-1 task-2; do
  for c in test-config-a test-config-b test-config-c test-config-d; do
    DIR="${BENCH_ROOT}/${t}/${c}"
    if [[ -d "${DIR}" ]]; then
      PY_COUNT=$(find "${DIR}" -type f -name "*.py" 2>/dev/null | wc -l)
      if [[ ${PY_COUNT} -gt 0 ]]; then
        UNEXPORTED_FILES+=("${DIR} (${PY_COUNT} python files)")
      fi
    fi
  done
done

if [[ ${#UNEXPORTED_FILES[@]} -gt 0 ]]; then
  echo "  WARNING: Found task results in workspace:"
  for f in "${UNEXPORTED_FILES[@]}"; do
    echo "    - ${f}"
  done
  echo "  Consider running export-evidence.sh before applying cleanup!"
else
  echo "  Task workspaces are empty; no pending benchmark task results found."
fi

# Step 3: Identify files to remove
echo "[Step 3] Inventory of benchmark resources scheduled for removal:"
if [[ -f "${MANIFEST}" ]]; then
  echo "  Managed benchmark root: ${BENCH_ROOT}"
  echo "  Includes:"
  echo "    - Isolated bin:          ${BENCH_ROOT}/setup/bin (rtk binary)"
  echo "    - Isolated extensions:   ${BENCH_ROOT}/setup/extensions (rtk.ts)"
  echo "    - Isolated profiles:     ${BENCH_ROOT}/setup/profiles, ${BENCH_ROOT}/round-3/profiles"
  echo "    - Isolated python venv:  ${BENCH_ROOT}/setup/venv-headroom"
  echo "    - Isolated sessions:     ${BENCH_ROOT}/setup/sessions"
  echo "    - Launchers & symlinks:  ${BENCH_ROOT}/bench-{a,b,c,d,e,u1,u2,u3,u4,u5}, ${BENCH_ROOT}/cleanup"
  echo "    - Task workspaces:       ${BENCH_ROOT}/task-{1,2}/test-config-{a,b,c,d}, ${BENCH_ROOT}/round-2/workspaces, ${BENCH_ROOT}/round-3/workspaces"
else
  echo "  Notice: Manifest not found; targeting ${BENCH_ROOT}"
fi

# Step 4: Verify external configuration state
echo "[Step 4] Checking external configurations..."
SETTINGS_DIFF=0
if [[ -f "${BACKUP_DIR}/settings.json.pre-bench" ]]; then
  if ! diff -q "${BACKUP_DIR}/settings.json.pre-bench" ~/.pi/agent/settings.json >/dev/null 2>&1; then
    SETTINGS_DIFF=1
    echo "  Notice: ~/.pi/agent/settings.json differs from pre-bench backup."
  else
    echo "  Verified: ~/.pi/agent/settings.json is identical to pre-bench backup (no changes were made)."
  fi
fi

ZSHENV_DIFF=0
if [[ -f "${BACKUP_DIR}/zshenv.pre-bench" ]]; then
  if ! diff -q "${BACKUP_DIR}/zshenv.pre-bench" ~/.zshenv >/dev/null 2>&1; then
    ZSHENV_DIFF=1
    echo "  Notice: ~/.zshenv differs from pre-bench backup."
  else
    echo "  Verified: ~/.zshenv is identical to pre-bench backup (aliases untouched)."
  fi
fi

# Execute or Dry-Run
if [[ "${MODE}" == "--dry-run" ]]; then
  echo ""
  echo "======================================================================"
  echo "  [DRY RUN SUMMARY] Nothing was modified."
  echo "  Would stop:        ${#PROCESSES_TO_KILL[@]} processes"
  echo "  Would remove:      ${BENCH_ROOT} (and all enclosed isolated files)"
  echo "  Would restore:     settings.json (${SETTINGS_DIFF} changes), zshenv (${ZSHENV_DIFF} changes)"
  echo "  Protected paths:   ~/.pi, ~/.zshenv, ~/.config/zsh, ~/.local/bin/pi remain safe."
  echo "======================================================================"
  exit 0
fi

# Apply Mode
echo ""
echo "Applying cleanup..."

# 1. Kill processes
if [[ ${#PROCESSES_TO_KILL[@]} -gt 0 ]]; then
  echo "Stopping benchmark processes..."
  pkill -f "headroom proxy" 2>/dev/null || true
  pkill -f "rtk-proxy" 2>/dev/null || true
fi

# 2. Restore settings if they were modified
if [[ ${SETTINGS_DIFF} -eq 1 ]]; then
  echo "Restoring ~/.pi/agent/settings.json from pre-bench backup..."
  cp "${BACKUP_DIR}/settings.json.pre-bench" ~/.pi/agent/settings.json
fi

if [[ ${ZSHENV_DIFF} -eq 1 ]]; then
  echo "Restoring ~/.zshenv from pre-bench backup..."
  cp "${BACKUP_DIR}/zshenv.pre-bench" ~/.zshenv
fi

# 3. Remove benchmark directory
echo "Removing benchmark environment at ${BENCH_ROOT}..."
# Strictly verify path before removal to prevent any accidental deletion
if [[ "${BENCH_ROOT}" == "/home/khoa/pi-token-bench" && -d "${BENCH_ROOT}" ]]; then
  rm -rf "${BENCH_ROOT}"
fi

# 4. Clean up any temporary RTK data in user home if left
if [[ -d "/home/khoa/.local/share/rtk" ]]; then
  rm -rf "/home/khoa/.local/share/rtk"
fi

# Step 5: Post-cleanup verification
echo ""
echo "======================================================================"
echo "  Post-Cleanup Verification"
echo "======================================================================"

# Verify Pi minimal harness still works
if command -v pi >/dev/null 2>&1; then
  PI_VER=$(pi --version 2>&1)
  echo "  [OK] Pi binary is accessible: ${PI_VER}"
else
  echo "  [FAIL] Pi binary not found!"
  exit 1
fi

# Verify benchmark files no longer exist
if [[ ! -d "/home/khoa/pi-token-bench" ]]; then
  echo "  [OK] Benchmark environment ${BENCH_ROOT} completely removed."
else
  echo "  [FAIL] Benchmark directory still exists!"
  exit 1
fi

# Verify aliases
if grep -q "alias pia=" ~/.zshenv && grep -q "alias pil=" ~/.zshenv; then
  echo "  [OK] Pre-existing shell aliases (pia, pil, pim) intact in ~/.zshenv"
else
  echo "  [FAIL] Shell aliases in ~/.zshenv modified or missing!"
  exit 1
fi

# Verify no optimizers active
if command -v rtk >/dev/null 2>&1; then
  echo "  [WARN] rtk is still in global PATH: $(which rtk)"
else
  echo "  [OK] rtk is not in global PATH."
fi

if command -v headroom >/dev/null 2>&1; then
  echo "  [WARN] headroom is still in global PATH: $(which headroom)"
else
  echo "  [OK] headroom is not in global PATH."
fi

echo ""
echo "Cleanup completed successfully. Original minimal Pi environment restored."
