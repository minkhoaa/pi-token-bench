#!/usr/bin/env python3
"""
Independent external evaluator for Task 7: Crash-Safe Event-Sourced Ledger.
Evaluates single-file modification constraint (src/ledger.py only),
public test discovery, and at least 20 hidden evaluation checks.
"""

import sys
sys.dont_write_bytecode = True

import concurrent.futures
import hashlib
import json
import multiprocessing
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


def compute_file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _concurrent_worker(workspace_dir: str, ledger_dir: str, worker_id: int, num_ops: int) -> int:
    """Worker process for concurrent ledger operations."""
    sys.path.insert(0, workspace_dir)
    try:
        from src.ledger import Ledger
        ledger = Ledger(Path(ledger_dir))
        success_count = 0
        for i in range(num_ops):
            tx_id = f"tx-worker-{worker_id}-{i}"
            acc = f"acc-{worker_id % 3}"
            try:
                ledger.credit(tx_id, acc, 10)
                success_count += 1
            except Exception:
                pass
        return success_count
    finally:
        if workspace_dir in sys.path:
            sys.path.remove(workspace_dir)


def evaluate_task_7(workspace_dir: str, fixture_dir: str) -> dict:
    ws_path = Path(workspace_dir).resolve()
    fix_path = Path(fixture_dir).resolve()

    ledger_file = ws_path / "src" / "ledger.py"

    result = {
        "task": "task-7",
        "workspace_dir": str(ws_path),
        "target_file": "src/ledger.py",
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

    if not ledger_file.is_file():
        result["failures"].append("Target file 'src/ledger.py' does not exist")
        return result

    result["target_exists"] = True
    content = ledger_file.read_bytes()
    result["line_count"] = len(content.splitlines())
    result["sha256"] = hashlib.sha256(content).hexdigest()

    # 1. Verify single file modification rule:
    # All files other than src/ledger.py must match fixture exactly.
    fix_files = {p.relative_to(fix_path).as_posix(): p for p in fix_path.rglob("*") if p.is_file()}
    ws_files = {p.relative_to(ws_path).as_posix(): p for p in ws_path.rglob("*") if p.is_file()}

    clean_ws = {k: v for k, v in ws_files.items() if not k.startswith(".git") and "__pycache__" not in k and not k.endswith(".pyc")}
    clean_fix = {k: v for k, v in fix_files.items() if not k.startswith(".git") and "__pycache__" not in k and not k.endswith(".pyc")}

    unexpected = []
    for rel_path, f_path in clean_fix.items():
        if rel_path == "src/ledger.py":
            continue
        if rel_path not in clean_ws:
            unexpected.append(f"Deleted required file: {rel_path}")
        else:
            if compute_file_hash(f_path) != compute_file_hash(clean_ws[rel_path]):
                unexpected.append(f"Modified unauthorized file: {rel_path}")

    for rel_path in clean_ws:
        if rel_path != "src/ledger.py" and rel_path not in clean_fix:
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
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        result["public_tests_output"] = proc.stderr + proc.stdout
        if proc.returncode == 0:
            result["public_tests_passed"] = True
        else:
            result["failures"].append(f"Public tests failed (exit code {proc.returncode})")
    except subprocess.TimeoutExpired:
        result["failures"].append("Public tests timed out after 30 seconds")
    except Exception as e:
        result["failures"].append(f"Public tests execution error: {str(e)}")

    # 3. Run Hidden Tests
    hidden_checks = []

    def run_check(name: str, fn):
        try:
            fn()
            hidden_checks.append((name, True, ""))
        except AssertionError as ae:
            hidden_checks.append((name, False, f"AssertionError: {str(ae)}"))
        except Exception as ex:
            hidden_checks.append((name, False, f"{type(ex).__name__}: {str(ex)}"))

    # Load module dynamically
    for p in list(sys.path):
        if "round-4" in p or "fixtures" in p or "workspaces" in p:
            try:
                sys.path.remove(p)
            except ValueError:
                pass
    sys.path.insert(0, str(ws_path))

    for mod in list(sys.modules.keys()):
        if mod.startswith("src.") or mod == "src":
            del sys.modules[mod]
    try:
        from src.exceptions import (
            ConflictingTransactionError,
            DuplicateTransactionError,
            InsufficientFundsError,
            InvalidAmountError,
            JournalCorruptionError,
            LedgerError,
        )
        from src.journal import Journal, compute_record_checksum
        from src.ledger import Ledger
        from src.models import Record

        # Check 1: Initial balance is 0 for non-existent accounts
        def check_initial_balance_zero():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h1_"))
            try:
                ledger = Ledger(temp_dir)
                assert ledger.balance("nonexistent") == 0, f"Expected 0, got {ledger.balance('nonexistent')}"
                assert ledger.balance("another") == 0
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_initial_balance_zero", check_initial_balance_zero)

        # Check 2: Credit adds funds and returns valid dict structure
        def check_credit_structure_and_balance():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h2_"))
            try:
                ledger = Ledger(temp_dir)
                res = ledger.credit("tx-c1", "alice", 150)
                assert isinstance(res, dict), "Result must be a dict"
                assert res.get("status") == "success"
                assert res.get("command") == "credit"
                assert res.get("transaction_id") == "tx-c1"
                assert res.get("account") == "alice"
                assert res.get("amount") == 150
                assert res.get("balance") == 150
                assert ledger.balance("alice") == 150
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_credit_structure_and_balance", check_credit_structure_and_balance)

        # Check 3: Multi-account credit isolation
        def check_multi_account_credits():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h3_"))
            try:
                ledger = Ledger(temp_dir)
                ledger.credit("tx-1", "user1", 100)
                ledger.credit("tx-2", "user2", 200)
                ledger.credit("tx-3", "user1", 50)
                assert ledger.balance("user1") == 150
                assert ledger.balance("user2") == 200
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_multi_account_credits", check_multi_account_credits)

        # Check 4: Atomic transfer between accounts
        def check_atomic_transfer():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h4_"))
            try:
                ledger = Ledger(temp_dir)
                ledger.credit("tx-1", "alice", 300)
                res = ledger.transfer("tx-t1", "alice", "bob", 120)
                assert res.get("status") == "success"
                assert res.get("command") == "transfer"
                assert res.get("source") == "alice"
                assert res.get("target") == "bob"
                assert res.get("amount") == 120
                assert res.get("source_balance") == 180
                assert res.get("target_balance") == 120
                assert ledger.balance("alice") == 180
                assert ledger.balance("bob") == 120
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_atomic_transfer", check_atomic_transfer)

        # Check 5: Insufficient funds raises InsufficientFundsError without mutating state
        def check_insufficient_funds_no_mutation():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h5_"))
            try:
                ledger = Ledger(temp_dir)
                ledger.credit("tx-1", "alice", 50)
                try:
                    ledger.transfer("tx-t1", "alice", "bob", 60)
                    assert False, "Should have raised InsufficientFundsError"
                except InsufficientFundsError:
                    pass
                assert ledger.balance("alice") == 50
                assert ledger.balance("bob") == 0
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_insufficient_funds_no_mutation", check_insufficient_funds_no_mutation)

        # Check 6: Rejected transfer does not write record to journal
        def check_rejected_transfer_no_journal_record():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h6_"))
            try:
                ledger = Ledger(temp_dir)
                ledger.credit("tx-1", "alice", 50)
                try:
                    ledger.transfer("tx-fail", "alice", "bob", 100)
                except InsufficientFundsError:
                    pass
                j = Journal(temp_dir / "ledger.journal")
                records = j.read_records()
                assert len(records) == 1, f"Expected 1 record in journal, found {len(records)}"
                assert records[0].tx_id == "tx-1"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_rejected_transfer_no_journal_record", check_rejected_transfer_no_journal_record)

        # Check 7: Invalid amount types (booleans True/False rejected)
        def check_reject_booleans():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h7_"))
            try:
                ledger = Ledger(temp_dir)
                try:
                    ledger.credit("tx-b1", "alice", True)
                    assert False, "Should have rejected True as amount"
                except InvalidAmountError:
                    pass
                try:
                    ledger.credit("tx-b2", "alice", False)
                    assert False, "Should have rejected False as amount"
                except InvalidAmountError:
                    pass
                assert ledger.balance("alice") == 0
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_reject_booleans", check_reject_booleans)

        # Check 8: Invalid amount types (floats and strings rejected)
        def check_reject_floats_and_strings():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h8_"))
            try:
                ledger = Ledger(temp_dir)
                try:
                    ledger.credit("tx-f", "alice", 10.5)
                    assert False, "Should have rejected float"
                except InvalidAmountError:
                    pass
                try:
                    ledger.credit("tx-s", "alice", "100")
                    assert False, "Should have rejected str"
                except InvalidAmountError:
                    pass
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_reject_floats_and_strings", check_reject_floats_and_strings)

        # Check 9: Non-positive amounts (zero, negative rejected)
        def check_reject_zero_and_negative():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h9_"))
            try:
                ledger = Ledger(temp_dir)
                try:
                    ledger.credit("tx-z", "alice", 0)
                    assert False, "Should have rejected 0"
                except InvalidAmountError:
                    pass
                try:
                    ledger.credit("tx-n", "alice", -10)
                    assert False, "Should have rejected negative"
                except InvalidAmountError:
                    pass
                try:
                    ledger.transfer("tx-tz", "alice", "bob", 0)
                    assert False, "Should have rejected transfer of 0"
                except InvalidAmountError:
                    pass
                try:
                    ledger.transfer("tx-tn", "alice", "bob", -5)
                    assert False, "Should have rejected transfer of negative"
                except InvalidAmountError:
                    pass
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_reject_zero_and_negative", check_reject_zero_and_negative)

        # Check 10: Idempotent duplicate credit
        def check_idempotent_duplicate_credit():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h10_"))
            try:
                ledger = Ledger(temp_dir)
                r1 = ledger.credit("tx-dup", "alice", 100)
                r2 = ledger.credit("tx-dup", "alice", 100)
                assert r1 == r2, f"Duplicate credit returned different result: {r1} vs {r2}"
                assert ledger.balance("alice") == 100, f"Balance was double credited: {ledger.balance('alice')}"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_idempotent_duplicate_credit", check_idempotent_duplicate_credit)

        # Check 11: Idempotent duplicate transfer
        def check_idempotent_duplicate_transfer():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h11_"))
            try:
                ledger = Ledger(temp_dir)
                ledger.credit("tx-c", "alice", 200)
                r1 = ledger.transfer("tx-t-dup", "alice", "bob", 80)
                r2 = ledger.transfer("tx-t-dup", "alice", "bob", 80)
                assert r1 == r2, f"Duplicate transfer returned different result: {r1} vs {r2}"
                assert ledger.balance("alice") == 120
                assert ledger.balance("bob") == 80
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_idempotent_duplicate_transfer", check_idempotent_duplicate_transfer)

        # Check 12: Conflicting transaction ID with different amount
        def check_conflicting_amount():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h12_"))
            try:
                ledger = Ledger(temp_dir)
                ledger.credit("tx-conflict", "alice", 100)
                try:
                    ledger.credit("tx-conflict", "alice", 200)
                    assert False, "Should have raised ConflictingTransactionError"
                except ConflictingTransactionError:
                    pass
                assert ledger.balance("alice") == 100
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_conflicting_amount", check_conflicting_amount)

        # Check 13: Conflicting transaction ID with different account
        def check_conflicting_account():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h13_"))
            try:
                ledger = Ledger(temp_dir)
                ledger.credit("tx-c1", "alice", 100)
                try:
                    ledger.credit("tx-c1", "bob", 100)
                    assert False, "Should have raised ConflictingTransactionError"
                except ConflictingTransactionError:
                    pass
                assert ledger.balance("alice") == 100
                assert ledger.balance("bob") == 0
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_conflicting_account", check_conflicting_account)

        # Check 14: Conflicting transaction ID between credit and transfer
        def check_conflicting_command():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h14_"))
            try:
                ledger = Ledger(temp_dir)
                ledger.credit("tx-switch", "alice", 200)
                try:
                    ledger.transfer("tx-switch", "alice", "bob", 50)
                    assert False, "Should have raised ConflictingTransactionError"
                except ConflictingTransactionError:
                    pass
                assert ledger.balance("alice") == 200
                assert ledger.balance("bob") == 0
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_conflicting_command", check_conflicting_command)

        # Check 15: Replay reconstructs accurate state on fresh Ledger instance
        def check_replay_accuracy():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h15_"))
            try:
                l1 = Ledger(temp_dir)
                l1.credit("tx-1", "alice", 500)
                l1.credit("tx-2", "bob", 300)
                l1.transfer("tx-3", "alice", "charlie", 150)
                l1.transfer("tx-4", "bob", "charlie", 50)

                l2 = Ledger(temp_dir)
                replayed = l2.replay()
                assert replayed == {"alice": 350, "bob": 250, "charlie": 200}
                assert l2.balance("alice") == 350
                assert l2.balance("bob") == 250
                assert l2.balance("charlie") == 200
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_replay_accuracy", check_replay_accuracy)

        # Check 16: Middle record journal corruption raises JournalCorruptionError
        def check_middle_record_corruption():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h16_"))
            try:
                l1 = Ledger(temp_dir)
                l1.credit("tx-1", "alice", 100)
                l1.credit("tx-2", "alice", 200)
                l1.credit("tx-3", "alice", 300)

                # Corrupt the 2nd record in the file
                j_file = temp_dir / "ledger.journal"
                lines = j_file.read_text(encoding="utf-8").splitlines()
                # Tamper with record 1
                rec1 = json.loads(lines[1])
                rec1["amount"] = 9999  # Checksum will now mismatch
                lines[1] = json.dumps(rec1)
                j_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

                try:
                    l2 = Ledger(temp_dir)
                    assert False, "Should have raised JournalCorruptionError on corrupted middle record"
                except JournalCorruptionError:
                    pass
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_middle_record_corruption", check_middle_record_corruption)

        # Check 17: Tampered checksum detected
        def check_tampered_checksum():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h17_"))
            try:
                l1 = Ledger(temp_dir)
                l1.credit("tx-1", "alice", 100)
                l1.credit("tx-2", "bob", 200)

                j_file = temp_dir / "ledger.journal"
                lines = j_file.read_text(encoding="utf-8").splitlines()
                rec0 = json.loads(lines[0])
                rec0["checksum"] = "badchecksum000000000000000000000000000000000000000000000000000000"
                lines[0] = json.dumps(rec0)
                j_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

                try:
                    l2 = Ledger(temp_dir)
                    assert False, "Should have raised JournalCorruptionError on tampered checksum"
                except JournalCorruptionError:
                    pass
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_tampered_checksum", check_tampered_checksum)

        # Check 18: Trailing truncated record at EOF recovered cleanly without crashing
        def check_trailing_truncated_recovery():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h18_"))
            try:
                l1 = Ledger(temp_dir)
                l1.credit("tx-1", "alice", 100)
                l1.credit("tx-2", "bob", 200)

                # Append half a JSON record simulating crash mid-write
                j_file = temp_dir / "ledger.journal"
                with open(j_file, "a", encoding="utf-8") as f:
                    f.write('{"index": 2, "command": "credit", "tx_id": "tx-truncated", "acc')

                # Reopening Ledger should discard trailing truncated record and recover valid state
                l2 = Ledger(temp_dir)
                assert l2.balance("alice") == 100
                assert l2.balance("bob") == 200
                assert l2.balance("truncated") == 0
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_trailing_truncated_recovery", check_trailing_truncated_recovery)

        # Check 19: Transfer to same account rejected
        def check_transfer_same_account():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h19_"))
            try:
                ledger = Ledger(temp_dir)
                ledger.credit("tx-1", "alice", 100)
                try:
                    ledger.transfer("tx-2", "alice", "alice", 50)
                    assert False, "Should have rejected transfer to same account"
                except (LedgerError, ValueError):
                    pass
                assert ledger.balance("alice") == 100
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_transfer_same_account", check_transfer_same_account)

        # Check 20: Concurrency safety across multiprocessing workers
        def check_multiprocess_concurrency():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h20_"))
            try:
                num_workers = 4
                ops_per_worker = 15
                worker_script = (
                    "import sys\n"
                    "from pathlib import Path\n"
                    "sys.path.insert(0, sys.argv[1])\n"
                    "from src.ledger import Ledger\n"
                    "ledger = Ledger(Path(sys.argv[2]))\n"
                    "worker_id = int(sys.argv[3])\n"
                    "ops = int(sys.argv[4])\n"
                    "success = 0\n"
                    "for i in range(ops):\n"
                    "    try:\n"
                    "        ledger.credit(f'tx-w-{worker_id}-{i}', f'acc-{worker_id % 3}', 10)\n"
                    "        success += 1\n"
                    "    except Exception:\n"
                    "        pass\n"
                    "print(success)\n"
                )

                procs = []
                for w in range(num_workers):
                    p = subprocess.Popen(
                        [sys.executable, "-B", "-c", worker_script, str(ws_path), str(temp_dir), str(w), str(ops_per_worker)],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    procs.append(p)

                results = []
                for p in procs:
                    stdout, stderr = p.communicate(timeout=25)
                    assert p.returncode == 0, f"Worker process failed with code {p.returncode}: {stderr}"
                    results.append(int(stdout.strip()))

                total_successful = sum(results)
                assert total_successful == num_workers * ops_per_worker, f"Expected {num_workers * ops_per_worker}, got {total_successful}"

                # Replay verification
                verifier = Ledger(temp_dir)
                total_balance = sum(verifier.balance(f"acc-{k}") for k in range(3))
                assert total_balance == total_successful * 10, f"Expected total {total_successful * 10}, got {total_balance}"
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_multiprocess_concurrency", check_multiprocess_concurrency)

        # Check 21: Durability across process restarts
        def check_durability_across_restarts():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h21_"))
            try:
                l1 = Ledger(temp_dir)
                for i in range(10):
                    l1.credit(f"tx-d-{i}", "vault", 25)
                l1.transfer("tx-d-out", "vault", "reserve", 100)

                # Delete object reference simulating process termination
                del l1

                l2 = Ledger(temp_dir)
                assert l2.balance("vault") == 150
                assert l2.balance("reserve") == 100
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_durability_across_restarts", check_durability_across_restarts)

        # Check 22: Invalid transaction_id or account types rejected
        def check_invalid_identifiers():
            temp_dir = Path(tempfile.mkdtemp(prefix="test_h22_"))
            try:
                ledger = Ledger(temp_dir)
                try:
                    ledger.credit("", "alice", 100)
                    assert False, "Empty tx_id should fail"
                except Exception:
                    pass
                try:
                    ledger.credit("tx-valid", "", 100)
                    assert False, "Empty account should fail"
                except Exception:
                    pass
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
        run_check("check_invalid_identifiers", check_invalid_identifiers)

    finally:
        if str(ws_path) in sys.path:
            sys.path.remove(str(ws_path))

    result["hidden_total"] = len(hidden_checks)
    result["hidden_passed"] = sum(1 for _, ok, _ in hidden_checks if ok)

    failed_checks = [f"{name}: {err}" for name, ok, err in hidden_checks if not ok]
    if failed_checks:
        result["failures"].extend(failed_checks)

    if result["single_file_modified"] and result["public_tests_passed"] and (result["hidden_passed"] == result["hidden_total"]):
        result["hidden_tests_passed"] = True
        result["status"] = "PASS"
    else:
        result["status"] = "FAIL"

    return result


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <workspace_dir> <fixture_dir>")
        sys.exit(1)

    res = evaluate_task_7(sys.argv[1], sys.argv[2])
    print(json.dumps(res, indent=2))
    sys.exit(0 if res["status"] == "PASS" else 1)
