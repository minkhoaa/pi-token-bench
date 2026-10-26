#!/usr/bin/env bash
# Post-Benchmark Winner Selection Mechanism
# Allows user to either restore minimal Pi (Option A) or retain only the winning optimizer (Option B).
set -euo pipefail

BENCH_ROOT="/home/khoa/pi-token-bench"
SETUP_DIR="${BENCH_ROOT}/setup"
BACKUP_DIR="${SETUP_DIR}/backups"

WINNER="${1:-}"

print_usage() {
  echo "Post-Benchmark Selection Utility"
  echo ""
  echo "Usage:"
  echo "  select-winner.sh none               # Option A: Restore completely minimal Pi (Default)"
  echo "  select-winner.sh rtk                # Option B: Keep only RTK (on-demand)"
  echo "  select-winner.sh ponytail           # Option B: Keep only Ponytail Lite (on-demand)"
  echo "  select-winner.sh caveman            # Option B: Keep only Caveman Lite (on-demand)"
  echo "  select-winner.sh combined           # Option B: Keep Ponytail Lite + Caveman Lite + RTK"
  echo ""
}

if [[ -z "${WINNER}" ]]; then
  print_usage
  exit 1
fi

case "${WINNER}" in
  none|restore|cleanup)
    echo "Selected Outcome: Option A — Restore completely minimal Pi"
    exec "${SETUP_DIR}/scripts/cleanup.sh" --apply
    ;;

  rtk)
    echo "Selected Outcome: Option B — Retain only RTK (on-demand activation)"
    echo "Installing RTK binary to ~/.local/bin/rtk..."
    cp "${SETUP_DIR}/bin/rtk" "${HOME}/.local/bin/rtk"
    chmod +x "${HOME}/.local/bin/rtk"

    mkdir -p "${HOME}/.pi/agent/extensions"
    cp "${SETUP_DIR}/extensions/rtk.ts" "${HOME}/.pi/agent/extensions/rtk.ts"
    echo "RTK extension installed to ~/.pi/agent/extensions/rtk.ts"
    echo "On-demand usage:"
    echo "  pi -e ~/.pi/agent/extensions/rtk.ts"
    echo ""
    echo "Removing losing optimizations (Ponytail, Caveman, Headroom) and benchmark scaffolding..."
    rm -rf "${BENCH_ROOT}"
    echo "Done. Winner RTK installed on-demand. Original minimal Pi baseline preserved."
    ;;

  ponytail)
    echo "Selected Outcome: Option B — Retain only Ponytail Lite (on-demand instructions)"
    mkdir -p "${HOME}/.pi/agent/prompts"
    cp "${SETUP_DIR}/profiles/ponytail-lite.md" "${HOME}/.pi/agent/prompts/ponytail-lite.md"
    echo "Ponytail Lite instructions saved to ~/.pi/agent/prompts/ponytail-lite.md"
    echo "On-demand usage:"
    echo "  pi --append-system-prompt ~/.pi/agent/prompts/ponytail-lite.md"
    echo ""
    echo "Removing losing optimizations (RTK, Caveman, Headroom) and benchmark scaffolding..."
    rm -rf "${BENCH_ROOT}"
    echo "Done. Winner Ponytail Lite installed on-demand."
    ;;

  caveman)
    echo "Selected Outcome: Option B — Retain only Caveman Lite (on-demand instructions)"
    mkdir -p "${HOME}/.pi/agent/prompts"
    cp "${SETUP_DIR}/profiles/caveman-lite.md" "${HOME}/.pi/agent/prompts/caveman-lite.md"
    echo "Caveman Lite instructions saved to ~/.pi/agent/prompts/caveman-lite.md"
    echo "On-demand usage:"
    echo "  pi --append-system-prompt ~/.pi/agent/prompts/caveman-lite.md"
    echo ""
    echo "Removing losing optimizations (RTK, Ponytail, Headroom) and benchmark scaffolding..."
    rm -rf "${BENCH_ROOT}"
    echo "Done. Winner Caveman Lite installed on-demand."
    ;;

  combined)
    echo "Selected Outcome: Option B — Retain Combined Optimizers (Ponytail + Caveman + RTK)"
    cp "${SETUP_DIR}/bin/rtk" "${HOME}/.local/bin/rtk"
    chmod +x "${HOME}/.local/bin/rtk"
    mkdir -p "${HOME}/.pi/agent/extensions" "${HOME}/.pi/agent/prompts"
    cp "${SETUP_DIR}/extensions/rtk.ts" "${HOME}/.pi/agent/extensions/rtk.ts"
    cp "${SETUP_DIR}/profiles/combined-lite.md" "${HOME}/.pi/agent/prompts/combined-lite.md"
    echo "RTK and Combined Lite profile installed on-demand."
    echo "On-demand usage:"
    echo "  pi --append-system-prompt ~/.pi/agent/prompts/combined-lite.md -e ~/.pi/agent/extensions/rtk.ts"
    echo ""
    echo "Removing losing experimental packages (Headroom) and benchmark scaffolding..."
    rm -rf "${BENCH_ROOT}"
    echo "Done. Selected optimizers installed on-demand."
    ;;

  *)
    echo "Error: Unknown winner option '${WINNER}'"
    print_usage
    exit 1
    ;;
esac
