#!/usr/bin/env python3
"""
Independent external evaluator for Task 4: Crash-Safe Incremental File Indexer.
Evaluates single-file modification constraint (src/indexer.py only),
public test discovery, and 18 hidden evaluation checks.
"""

import sys
sys.dont_write_bytecode = True

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def compute_file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def evaluate_task_4(workspace_dir: str, fixture_dir: str) -> dict:
    ws_path = Path(workspace_dir).resolve()
    fix_path = Path(fixture_dir).resolve()

    res_file = ws_path / "src" / "indexer.py"

    result = {
        "task": "task-4",
        "workspace_dir": str(ws_path),
        "target_file": "src/indexer.py",
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
        result["failures"].append("Target file 'src/indexer.py' does not exist")
        return result

    result["target_exists"] = True
    content = res_file.read_bytes()
    result["line_count"] = len(content.splitlines())
    result["sha256"] = hashlib.sha256(content).hexdigest()

    # 1. Verify single file modification rule:
    fix_files = {p.relative_to(fix_path).as_posix(): p for p in fix_path.rglob("*") if p.is_file()}
    ws_files = {p.relative_to(ws_path).as_posix(): p for p in ws_path.rglob("*") if p.is_file()}

    clean_ws = {k: v for k, v in ws_files.items() if not k.startswith(".git") and "__pycache__" not in k}
    clean_fix = {k: v for k, v in fix_files.items() if not k.startswith(".git") and "__pycache__" not in k}

    unexpected = []
    for rel_path, f_path in clean_fix.items():
        if rel_path == "src/indexer.py":
            continue
        if rel_path not in clean_ws:
            unexpected.append(f"Deleted required file: {rel_path}")
        else:
            if compute_file_hash(f_path) != compute_file_hash(clean_ws[rel_path]):
                unexpected.append(f"Modified unauthorized file: {rel_path}")

    for rel_path in clean_ws:
        if rel_path != "src/indexer.py" and rel_path not in clean_fix:
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
    if str(ws_path) in sys.path:
        sys.path.remove(str(ws_path))
    sys.path.insert(0, str(ws_path))

    for mod in list(sys.modules.keys()):
        if mod.startswith("src.") or mod == "src":
            del sys.modules[mod]

    try:
        import src.exceptions as exc_mod
        import src.indexer as indexer_mod
        import src.manifest as manifest_mod
    except Exception as e:
        result["failures"].append(f"Import of workspace modules failed: {e}")
        return result

    sync_index = indexer_mod.sync_index
    load_manifest = manifest_mod.load_manifest
    CorruptManifestError = exc_mod.CorruptManifestError
    IndexerError = exc_mod.IndexerError

    def run_check(name: str, fn):
        result["hidden_total"] += 1
        try:
            fn()
            result["hidden_passed"] += 1
        except Exception as e:
            result["failures"].append(f"Hidden check '{name}' failed: {e}")

    # Check 1: Empty root
    def check_empty_root():
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "root"
            r.mkdir()
            m = Path(td) / "manifest.json"
            res = sync_index(r, m)
            assert res == {"added": [], "modified": [], "deleted": [], "unchanged": []}
            assert m.is_file()
            assert load_manifest(m) == {}
    run_check("empty_root", check_empty_root)

    # Check 2: Initial indexing with multiple txt files
    def check_initial_index():
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "root"
            r.mkdir()
            m = Path(td) / "manifest.json"
            (r / "b.txt").write_text("b", encoding="utf-8")
            (r / "a.txt").write_text("a", encoding="utf-8")
            res = sync_index(r, m)
            assert res["added"] == ["a.txt", "b.txt"], f"Not sorted or wrong: {res['added']}"
            assert res["modified"] == []
            assert res["deleted"] == []
            assert res["unchanged"] == []
            files = load_manifest(m)
            assert "a.txt" in files and "b.txt" in files
    run_check("initial_indexing_sorted", check_initial_index)

    # Check 3: Non-txt files ignored
    def check_ignore_non_txt():
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "root"
            r.mkdir()
            m = Path(td) / "manifest.json"
            (r / "valid.txt").write_text("ok", encoding="utf-8")
            (r / "notes.md").write_text("skip", encoding="utf-8")
            (r / "app.py").write_text("skip", encoding="utf-8")
            (r / "no_ext").write_text("skip", encoding="utf-8")
            res = sync_index(r, m)
            assert res["added"] == ["valid.txt"]
            files = load_manifest(m)
            assert list(files.keys()) == ["valid.txt"]
    run_check("ignore_non_txt", check_ignore_non_txt)

    # Check 4: POSIX normalization on nested subdirectories
    def check_posix_paths():
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "root"
            r.mkdir()
            m = Path(td) / "manifest.json"
            sub = r / "dir1" / "dir2"
            sub.mkdir(parents=True)
            (sub / "nested.txt").write_text("nested", encoding="utf-8")
            res = sync_index(r, m)
            assert res["added"] == ["dir1/dir2/nested.txt"]
            assert "\\" not in res["added"][0]
    run_check("posix_path_normalization", check_posix_paths)

    # Check 5: Second run without changes
    def check_unchanged_second_run():
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "root"
            r.mkdir()
            m = Path(td) / "manifest.json"
            (r / "a.txt").write_text("a", encoding="utf-8")
            (r / "b.txt").write_text("b", encoding="utf-8")
            sync_index(r, m)
            res2 = sync_index(r, m)
            assert res2["added"] == []
            assert res2["modified"] == []
            assert res2["deleted"] == []
            assert res2["unchanged"] == ["a.txt", "b.txt"]
    run_check("unchanged_second_run", check_unchanged_second_run)

    # Check 6: Modification detected by hash
    def check_modification():
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "root"
            r.mkdir()
            m = Path(td) / "manifest.json"
            f = r / "edit.txt"
            f.write_text("v1", encoding="utf-8")
            sync_index(r, m)

            f.write_text("v2", encoding="utf-8")
            res = sync_index(r, m)
            assert res["modified"] == ["edit.txt"]
            assert res["added"] == []
            assert res["unchanged"] == []
    run_check("modification_detection", check_modification)

    # Check 7: Timestamp changed but identical content -> UNCHANGED
    def check_mtime_change_identical_content():
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "root"
            r.mkdir()
            m = Path(td) / "manifest.json"
            f = r / "same.txt"
            f.write_text("identical content", encoding="utf-8")
            sync_index(r, m)

            # Change mtime without altering content
            new_time = os.stat(f).st_mtime + 1000
            os.utime(f, (new_time, new_time))

            res = sync_index(r, m)
            assert res["unchanged"] == ["same.txt"], f"Mtime change misclassified as: {res}"
            assert res["modified"] == []
    run_check("mtime_change_identical_content", check_mtime_change_identical_content)

    # Check 8: Deletion detected
    def check_deletion():
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "root"
            r.mkdir()
            m = Path(td) / "manifest.json"
            (r / "keep.txt").write_text("keep", encoding="utf-8")
            (r / "del.txt").write_text("del", encoding="utf-8")
            sync_index(r, m)

            (r / "del.txt").unlink()
            res = sync_index(r, m)
            assert res["deleted"] == ["del.txt"]
            assert res["unchanged"] == ["keep.txt"]
            files = load_manifest(m)
            assert "del.txt" not in files
            assert "keep.txt" in files
    run_check("deletion_detection", check_deletion)

    # Check 9: Simultaneous added, modified, deleted, unchanged
    def check_mixed():
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "root"
            r.mkdir()
            m = Path(td) / "manifest.json"
            (r / "u.txt").write_text("u", encoding="utf-8")
            (r / "m.txt").write_text("m1", encoding="utf-8")
            (r / "d.txt").write_text("d", encoding="utf-8")
            sync_index(r, m)

            (r / "m.txt").write_text("m2", encoding="utf-8")
            (r / "d.txt").unlink()
            (r / "a.txt").write_text("a", encoding="utf-8")

            res = sync_index(r, m)
            assert res["added"] == ["a.txt"]
            assert res["modified"] == ["m.txt"]
            assert res["deleted"] == ["d.txt"]
            assert res["unchanged"] == ["u.txt"]
    run_check("mixed_lifecycle_changes", check_mixed)

    # Check 10: Symlink to file ignored
    def check_symlink_file():
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "root"
            r.mkdir()
            m = Path(td) / "manifest.json"
            real = r / "real.txt"
            real.write_text("real", encoding="utf-8")
            link = r / "link.txt"
            link.symlink_to(real)

            res = sync_index(r, m)
            assert res["added"] == ["real.txt"], f"Symlink was not ignored: {res['added']}"
    run_check("symlink_file_ignored", check_symlink_file)

    # Check 11: Symlink pointing outside root ignored
    def check_symlink_outside():
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "root"
            r.mkdir()
            m = Path(td) / "manifest.json"
            out = Path(td) / "outside.txt"
            out.write_text("outside", encoding="utf-8")
            link = r / "ext_link.txt"
            link.symlink_to(out)

            res = sync_index(r, m)
            assert res["added"] == []
    run_check("symlink_outside_ignored", check_symlink_outside)

    # Check 12: Directory symlink loop safety
    def check_symlink_dir_loop():
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "root"
            r.mkdir()
            m = Path(td) / "manifest.json"
            (r / "doc.txt").write_text("doc", encoding="utf-8")
            loop = r / "loop_dir"
            loop.symlink_to(r, target_is_directory=True)

            res = sync_index(r, m)
            assert res["added"] == ["doc.txt"], f"Directory symlink loop caused extra entries: {res['added']}"
    run_check("symlink_dir_loop_safety", check_symlink_dir_loop)

    # Check 13: Unicode filenames
    def check_unicode():
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "root"
            r.mkdir()
            m = Path(td) / "manifest.json"
            (r / "données_clés.txt").write_text("accent", encoding="utf-8")
            (r / "日本語_メモ.txt").write_text("kanji", encoding="utf-8")
            (r / "file with spaces and 🚀.txt").write_text("spaces", encoding="utf-8")

            res = sync_index(r, m)
            assert len(res["added"]) == 3
            files = load_manifest(m)
            assert len(files) == 3
            # Check re-read is unchanged
            res2 = sync_index(r, m)
            assert len(res2["unchanged"]) == 3
    run_check("unicode_filenames", check_unicode)

    # Check 14: Atomic manifest replacement & no temp artifacts
    def check_atomic_replace():
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "root"
            r.mkdir()
            m_dir = Path(td) / "manifest_dir"
            m_dir.mkdir()
            m = m_dir / "manifest.json"

            (r / "test.txt").write_text("test", encoding="utf-8")
            sync_index(r, m)

            # Confirm no leftover temp files in manifest directory
            files = list(m_dir.iterdir())
            assert len(files) == 1 and files[0].name == "manifest.json", f"Leftover files found: {files}"
    run_check("atomic_manifest_no_leftover_temps", check_atomic_replace)

    # Check 15: Corrupt manifest rejection
    def check_corrupt_manifest():
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "root"
            r.mkdir()
            m = Path(td) / "manifest.json"
            bad_content = "CORRUPT JSON CONTENT {"
            m.write_text(bad_content, encoding="utf-8")
            (r / "a.txt").write_text("content", encoding="utf-8")

            try:
                sync_index(r, m)
                assert False, "Did not raise CorruptManifestError on corrupt JSON"
            except CorruptManifestError:
                pass

            assert m.read_text(encoding="utf-8") == bad_content, "Corrupted manifest was modified or overwritten!"
    run_check("corrupt_manifest_rejection", check_corrupt_manifest)

    # Check 16: Manifest with invalid schema rejection
    def check_invalid_schema():
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "root"
            r.mkdir()
            m = Path(td) / "manifest.json"
            m.write_text(json.dumps({"version": 999, "files": "not_a_dict"}), encoding="utf-8")
            (r / "a.txt").write_text("content", encoding="utf-8")

            try:
                sync_index(r, m)
                assert False, "Did not raise CorruptManifestError on invalid schema"
            except CorruptManifestError:
                pass
    run_check("invalid_schema_rejection", check_invalid_schema)

    # Check 17: Large file chunked streaming (no whole-file read into memory)
    def check_chunked_streaming():
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "root"
            r.mkdir()
            m = Path(td) / "manifest.json"
            large_file = r / "large.txt"
            # 1 MB of repeated text
            chunk_data = b"0123456789ABCDEF" * 4096  # 64 KB
            with open(large_file, "wb") as f:
                for _ in range(16):  # 1 MB
                    f.write(chunk_data)

            res = sync_index(r, m)
            assert res["added"] == ["large.txt"]
            files = load_manifest(m)
            assert "large.txt" in files
    run_check("large_file_streaming", check_chunked_streaming)

    # Check 18: Empty subdirectories ignored
    def check_empty_subdirs():
        with tempfile.TemporaryDirectory() as td:
            r = Path(td) / "root"
            r.mkdir()
            m = Path(td) / "manifest.json"
            (r / "empty_dir1").mkdir()
            (r / "empty_dir2" / "nested_empty").mkdir(parents=True)
            (r / "real.txt").write_text("real", encoding="utf-8")

            res = sync_index(r, m)
            assert res["added"] == ["real.txt"]
    run_check("empty_subdirectories_ignored", check_empty_subdirs)

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
        print("Usage: eval_task_4.py <workspace-dir> <fixture-dir>")
        sys.exit(1)
    res = evaluate_task_4(sys.argv[1], sys.argv[2])
    print(json.dumps(res, indent=2))
    sys.exit(0 if res["status"] == "PASS" else 1)
