#!/usr/bin/env python3
"""
Independent external evaluator for Task 3: Concurrent Inventory Reservation.
Evaluates single-file modification constraint (src/reservations.py only),
public test discovery, and 18 hidden evaluation checks.
"""

import sys
sys.dont_write_bytecode = True

import concurrent.futures
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from pathlib import Path


def compute_file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def evaluate_task_3(workspace_dir: str, fixture_dir: str) -> dict:
    ws_path = Path(workspace_dir).resolve()
    fix_path = Path(fixture_dir).resolve()

    res_file = ws_path / "src" / "reservations.py"

    result = {
        "task": "task-3",
        "workspace_dir": str(ws_path),
        "target_file": "src/reservations.py",
        "target_exists": False,
        "single_file_modified": False,
        "unexpected_modifications": [],
        "line_count": 0,
        "sha256": None,
        "public_tests_passed": False,
        "public_tests_output": "",
        "hidden_tests_passed": False,
        "hidden_total": 0,
        "hidden_passed": 0,
        "failures": [],
        "status": "FAIL",
    }

    if not res_file.is_file():
        result["failures"].append("Target file 'src/reservations.py' does not exist")
        return result

    result["target_exists"] = True
    content = res_file.read_bytes()
    result["line_count"] = len(content.splitlines())
    result["sha256"] = hashlib.sha256(content).hexdigest()

    # 1. Verify single file modification rule:
    # All files other than src/reservations.py must match the fixture exactly.
    fix_files = {p.relative_to(fix_path).as_posix(): p for p in fix_path.rglob("*") if p.is_file()}
    ws_files = {p.relative_to(ws_path).as_posix(): p for p in ws_path.rglob("*") if p.is_file()}

    # Ignore .git or __pycache__ if any
    clean_ws = {k: v for k, v in ws_files.items() if not k.startswith(".git") and "__pycache__" not in k}
    clean_fix = {k: v for k, v in fix_files.items() if not k.startswith(".git") and "__pycache__" not in k}

    unexpected = []
    for rel_path, f_path in clean_fix.items():
        if rel_path == "src/reservations.py":
            continue
        if rel_path not in clean_ws:
            unexpected.append(f"Deleted required file: {rel_path}")
        else:
            if compute_file_hash(f_path) != compute_file_hash(clean_ws[rel_path]):
                unexpected.append(f"Modified unauthorized file: {rel_path}")

    for rel_path in clean_ws:
        if rel_path != "src/reservations.py" and rel_path not in clean_fix:
            unexpected.append(f"Added unauthorized file: {rel_path}")

    if unexpected:
        result["unexpected_modifications"] = unexpected
        result["failures"].extend(unexpected)
    else:
        result["single_file_modified"] = True

    # 2. Run Public Tests
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(ws_path)

    try:
        proc = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"],
            cwd=str(ws_path),
            capture_output=True,
            text=True,
            timeout=45,
            env=env,
        )
        combined_pub = (proc.stdout + "\n" + proc.stderr).strip()
        result["public_tests_output"] = combined_pub
        if proc.returncode == 0:
            result["public_tests_passed"] = True
        else:
            result["failures"].append(f"Public test discovery failed with exit code {proc.returncode}")
    except subprocess.TimeoutExpired:
        result["failures"].append("Public tests timed out after 45 seconds")
    except Exception as e:
        result["failures"].append(f"Public tests execution error: {e}")

    # 3. Dynamic import of workspace modules for hidden evaluation
    # Add workspace root to sys.path
    if str(ws_path) in sys.path:
        sys.path.remove(str(ws_path))
    sys.path.insert(0, str(ws_path))

    # Clear cached modules from src
    for mod in list(sys.modules.keys()):
        if mod.startswith("src.") or mod == "src":
            del sys.modules[mod]

    try:
        import src.db as db_mod
        import src.exceptions as exc_mod
        import src.reservations as res_mod
    except Exception as e:
        result["failures"].append(f"Import of workspace modules failed: {e}")
        return result

    init_db = db_mod.init_db
    seed_inventory = db_mod.seed_inventory
    get_connection = db_mod.get_connection

    reserve = res_mod.reserve
    cancel = res_mod.cancel
    get_available = res_mod.get_available

    InventoryError = exc_mod.InventoryError
    SkuNotFoundError = exc_mod.SkuNotFoundError
    InsufficientStockError = exc_mod.InsufficientStockError
    ReservationNotFoundError = exc_mod.ReservationNotFoundError
    InvalidReservationError = exc_mod.InvalidReservationError

    # Helper for running a hidden test case
    def run_check(name: str, fn):
        result["hidden_total"] += 1
        try:
            fn()
            result["hidden_passed"] += 1
        except Exception as e:
            result["failures"].append(f"Hidden check '{name}' failed: {e}")

    # Hidden Case 1: Basic reserve lifecycle
    def check_basic_reserve():
        with tempfile.TemporaryDirectory() as td:
            db_p = os.path.join(td, "test.db")
            init_db(db_p)
            seed_inventory(db_p, {"SKU-A": 50})
            r = reserve(db_p, "res-1", "SKU-A", 10)
            assert r["status"] == "active", f"Bad status: {r}"
            assert r["quantity"] == 10
            assert r["sku"] == "SKU-A"
            assert get_available(db_p, "SKU-A") == 40
    run_check("basic_reserve", check_basic_reserve)

    # Hidden Case 2: Basic cancel lifecycle
    def check_basic_cancel():
        with tempfile.TemporaryDirectory() as td:
            db_p = os.path.join(td, "test.db")
            init_db(db_p)
            seed_inventory(db_p, {"SKU-A": 50})
            reserve(db_p, "res-1", "SKU-A", 15)
            c = cancel(db_p, "res-1")
            assert c["status"] == "cancelled"
            assert get_available(db_p, "SKU-A") == 50
    run_check("basic_cancel", check_basic_cancel)

    # Hidden Case 3: Cancel non-existent
    def check_cancel_nonexistent():
        with tempfile.TemporaryDirectory() as td:
            db_p = os.path.join(td, "test.db")
            init_db(db_p)
            seed_inventory(db_p, {"SKU-A": 10})
            try:
                cancel(db_p, "ghost")
                assert False, "Did not raise ReservationNotFoundError"
            except ReservationNotFoundError:
                pass
    run_check("cancel_nonexistent", check_cancel_nonexistent)

    # Hidden Case 4: Idempotent reserve replay with matching parameters
    def check_idempotent_reserve():
        with tempfile.TemporaryDirectory() as td:
            db_p = os.path.join(td, "test.db")
            init_db(db_p)
            seed_inventory(db_p, {"SKU-A": 50})
            r1 = reserve(db_p, "idem-1", "SKU-A", 20)
            assert get_available(db_p, "SKU-A") == 30
            r2 = reserve(db_p, "idem-1", "SKU-A", 20)
            assert r2["status"] == "active"
            assert get_available(db_p, "SKU-A") == 30, "Replayed reservation deducted stock again!"
    run_check("idempotent_reserve_matching", check_idempotent_reserve)

    # Hidden Case 5: Conflicting idempotency key (different SKU)
    def check_conflict_sku():
        with tempfile.TemporaryDirectory() as td:
            db_p = os.path.join(td, "test.db")
            init_db(db_p)
            seed_inventory(db_p, {"SKU-A": 50, "SKU-B": 50})
            reserve(db_p, "idem-2", "SKU-A", 10)
            try:
                reserve(db_p, "idem-2", "SKU-B", 10)
                assert False, "Did not raise InvalidReservationError on SKU mismatch"
            except InvalidReservationError:
                pass
            assert get_available(db_p, "SKU-A") == 40
            assert get_available(db_p, "SKU-B") == 50
    run_check("conflicting_idempotency_sku", check_conflict_sku)

    # Hidden Case 6: Conflicting idempotency key (different quantity)
    def check_conflict_quantity():
        with tempfile.TemporaryDirectory() as td:
            db_p = os.path.join(td, "test.db")
            init_db(db_p)
            seed_inventory(db_p, {"SKU-A": 50})
            reserve(db_p, "idem-3", "SKU-A", 10)
            try:
                reserve(db_p, "idem-3", "SKU-A", 15)
                assert False, "Did not raise InvalidReservationError on quantity mismatch"
            except InvalidReservationError:
                pass
            assert get_available(db_p, "SKU-A") == 40
    run_check("conflicting_idempotency_quantity", check_conflict_quantity)

    # Hidden Case 7: Cannot reopen cancelled reservation
    def check_cannot_reopen_cancelled():
        with tempfile.TemporaryDirectory() as td:
            db_p = os.path.join(td, "test.db")
            init_db(db_p)
            seed_inventory(db_p, {"SKU-A": 50})
            reserve(db_p, "reopen-1", "SKU-A", 10)
            cancel(db_p, "reopen-1")
            assert get_available(db_p, "SKU-A") == 50
            try:
                reserve(db_p, "reopen-1", "SKU-A", 10)
                assert False, "Did not raise InvalidReservationError when replaying cancelled ID"
            except InvalidReservationError:
                pass
            assert get_available(db_p, "SKU-A") == 50, "Cancelled reservation re-deducted inventory!"
    run_check("cannot_reopen_cancelled", check_cannot_reopen_cancelled)

    # Hidden Case 8: Repeated cancellation never restores stock twice
    def check_repeated_cancel():
        with tempfile.TemporaryDirectory() as td:
            db_p = os.path.join(td, "test.db")
            init_db(db_p)
            seed_inventory(db_p, {"SKU-A": 50})
            reserve(db_p, "dc-1", "SKU-A", 20)
            assert get_available(db_p, "SKU-A") == 30
            c1 = cancel(db_p, "dc-1")
            assert get_available(db_p, "SKU-A") == 50
            c2 = cancel(db_p, "dc-1")
            assert get_available(db_p, "SKU-A") == 50, "Double cancel restored stock twice!"
            c3 = cancel(db_p, "dc-1")
            assert get_available(db_p, "SKU-A") == 50, "Triple cancel restored stock!"
    run_check("repeated_cancellation", check_repeated_cancel)

    # Hidden Case 9: Insufficient stock
    def check_insufficient_stock():
        with tempfile.TemporaryDirectory() as td:
            db_p = os.path.join(td, "test.db")
            init_db(db_p)
            seed_inventory(db_p, {"SKU-A": 5})
            try:
                reserve(db_p, "over-1", "SKU-A", 10)
                assert False, "Did not raise InsufficientStockError"
            except InsufficientStockError:
                pass
            assert get_available(db_p, "SKU-A") == 5
    run_check("insufficient_stock", check_insufficient_stock)

    # Hidden Case 10: Missing SKU
    def check_missing_sku():
        with tempfile.TemporaryDirectory() as td:
            db_p = os.path.join(td, "test.db")
            init_db(db_p)
            try:
                reserve(db_p, "r-1", "NO-SUCH-SKU", 1)
                assert False, "Did not raise SkuNotFoundError"
            except SkuNotFoundError:
                pass
            try:
                get_available(db_p, "NO-SUCH-SKU")
                assert False, "Did not raise SkuNotFoundError"
            except SkuNotFoundError:
                pass
    run_check("missing_sku", check_missing_sku)

    # Hidden Case 11: Invalid quantity validation (booleans, negative, zero)
    def check_invalid_quantity():
        with tempfile.TemporaryDirectory() as td:
            db_p = os.path.join(td, "test.db")
            init_db(db_p)
            seed_inventory(db_p, {"SKU-A": 50})
            for bad_q in (0, -1, True, False, "10", 2.5):
                try:
                    reserve(db_p, f"bad-q-{bad_q}", "SKU-A", bad_q)
                    assert False, f"Accepted invalid quantity: {bad_q}"
                except (InvalidReservationError, ValueError, TypeError):
                    pass
            assert get_available(db_p, "SKU-A") == 50
    run_check("invalid_quantity_validation", check_invalid_quantity)

    # Hidden Case 12: Invalid reservation ID
    def check_invalid_res_id():
        with tempfile.TemporaryDirectory() as td:
            db_p = os.path.join(td, "test.db")
            init_db(db_p)
            seed_inventory(db_p, {"SKU-A": 50})
            for bad_id in ("", "   ", None, 123):
                try:
                    reserve(db_p, bad_id, "SKU-A", 5)
                    assert False, f"Accepted invalid reservation_id: {bad_id}"
                except (InvalidReservationError, ValueError, TypeError):
                    pass
    run_check("invalid_res_id_validation", check_invalid_res_id)

    # Hidden Case 13: Concurrent competing reservations (oversubscription prevention)
    def check_concurrency_oversubscription():
        with tempfile.TemporaryDirectory() as td:
            db_p = os.path.join(td, "test.db")
            init_db(db_p)
            initial_stock = 15
            seed_inventory(db_p, {"HOT-ITEM": initial_stock})

            num_threads = 40
            barrier = threading.Barrier(num_threads)
            successes = 0
            failures = 0
            lock = threading.Lock()

            def client(i):
                nonlocal successes, failures
                barrier.wait(timeout=5)
                try:
                    reserve(db_p, f"conc-res-{i}", "HOT-ITEM", 1)
                    with lock:
                        successes += 1
                except InsufficientStockError:
                    with lock:
                        failures += 1

            threads = [threading.Thread(target=client, args=(i,)) for i in range(num_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            rem = get_available(db_p, "HOT-ITEM")
            assert successes == initial_stock, f"Expected {initial_stock} successes, got {successes}"
            assert failures == (num_threads - initial_stock), f"Expected failures {num_threads - initial_stock}, got {failures}"
            assert rem == 0, f"Available stock is not 0: {rem}"
    run_check("concurrency_oversubscription_prevention", check_concurrency_oversubscription)

    # Hidden Case 14: Concurrent identical reservation IDs
    def check_concurrency_identical_ids():
        with tempfile.TemporaryDirectory() as td:
            db_p = os.path.join(td, "test.db")
            init_db(db_p)
            seed_inventory(db_p, {"ITEM-X": 100})

            num_threads = 10
            barrier = threading.Barrier(num_threads)
            results = []
            lock = threading.Lock()

            def client():
                barrier.wait(timeout=5)
                res = reserve(db_p, "shared-idem-key", "ITEM-X", 10)
                with lock:
                    results.append(res)

            threads = [threading.Thread(target=client) for _ in range(num_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            assert len(results) == num_threads
            rem = get_available(db_p, "ITEM-X")
            assert rem == 90, f"Stock was deducted multiple times: remaining {rem} != 90"
    run_check("concurrency_identical_keys", check_concurrency_identical_ids)

    # Hidden Case 15: Concurrent cancellation of the same reservation
    def check_concurrency_cancel():
        with tempfile.TemporaryDirectory() as td:
            db_p = os.path.join(td, "test.db")
            init_db(db_p)
            seed_inventory(db_p, {"ITEM-Y": 100})
            reserve(db_p, "cancel-target", "ITEM-Y", 25)
            assert get_available(db_p, "ITEM-Y") == 75

            num_threads = 8
            barrier = threading.Barrier(num_threads)
            cancel_results = []
            lock = threading.Lock()

            def canceller():
                barrier.wait(timeout=5)
                res = cancel(db_p, "cancel-target")
                with lock:
                    cancel_results.append(res)

            threads = [threading.Thread(target=canceller) for _ in range(num_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            rem = get_available(db_p, "ITEM-Y")
            assert rem == 100, f"Stock was restored multiple times: remaining {rem} != 100"
    run_check("concurrency_cancel_safety", check_concurrency_cancel)

    # Hidden Case 16: Persistence across independent connections
    def check_persistence():
        with tempfile.TemporaryDirectory() as td:
            db_p = os.path.join(td, "test.db")
            init_db(db_p)
            seed_inventory(db_p, {"PERSIST-1": 50})
            reserve(db_p, "p-res-1", "PERSIST-1", 12)

            # Separate process to verify on-disk state
            code = (
                "import sys;"
                f"sys.path.insert(0, '{ws_path}');"
                "from src.reservations import get_available;"
                f"avail = get_available('{db_p}', 'PERSIST-1');"
                "assert avail == 38, f'got {avail}';"
            )
            proc = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
            assert proc.returncode == 0, f"Persistence check failed: {proc.stderr}"
    run_check("persistence_across_connections", check_persistence)

    # Hidden Case 17: Database integrity
    def check_db_integrity():
        with tempfile.TemporaryDirectory() as td:
            db_p = os.path.join(td, "test.db")
            init_db(db_p)
            seed_inventory(db_p, {"INTEG": 20})
            reserve(db_p, "integ-1", "INTEG", 5)
            cancel(db_p, "integ-1")

            conn = get_connection(db_p)
            try:
                row = conn.execute("PRAGMA integrity_check;").fetchone()
                assert row[0] == "ok", f"Integrity check failed: {row[0]}"
            finally:
                conn.close()
    run_check("database_integrity", check_db_integrity)

    # Hidden Case 18: Rollback on database constraint violation
    def check_rollback_integrity():
        with tempfile.TemporaryDirectory() as td:
            db_p = os.path.join(td, "test.db")
            init_db(db_p)
            seed_inventory(db_p, {"ROLLBACK-SKU": 10})
            try:
                reserve(db_p, "roll-1", "ROLLBACK-SKU", 15)
            except InsufficientStockError:
                pass
            conn = get_connection(db_p)
            try:
                row = conn.execute("SELECT * FROM reservations WHERE reservation_id = 'roll-1'").fetchone()
                assert row is None, "Failed reservation record was not rolled back!"
            finally:
                conn.close()
    run_check("rollback_integrity", check_rollback_integrity)

    if result["hidden_total"] > 0 and result["hidden_passed"] == result["hidden_total"]:
        result["hidden_tests_passed"] = True

    if (
        result["target_exists"]
        and result["single_file_modified"]
        and result["public_tests_passed"]
        and result["hidden_tests_passed"]
    ):
        result["status"] = "PASS"
    else:
        result["status"] = "FAIL"

    return result


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: eval_task_3.py <workspace-dir> <fixture-dir>")
        sys.exit(1)
    res = evaluate_task_3(sys.argv[1], sys.argv[2])
    print(json.dumps(res, indent=2))
    sys.exit(0 if res["status"] == "PASS" else 1)
