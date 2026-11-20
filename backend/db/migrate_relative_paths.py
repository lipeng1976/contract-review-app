"""Migrate absolute file paths in the DB to paths relative to CONTRACT_DATA_DIR.

Background
----------
Before 2026-11-20 the DB stored absolute paths like
  D:\\xinxin\\contract\\contract-review-app\\data\\contracts\\CT-xxx\\original.pdf
which pin files to one machine. Now all paths are relative to CONTRACT_DATA_DIR
(e.g. ``contracts/CT-xxx/original.pdf``). This script converts the existing rows.

Safety
------
* Default is DRY RUN: prints what would change, touches nothing.
* Requires ``--yes`` to actually write.
* Idempotent: rows already relative are skipped, so re-running is harmless.
* For every absolute path it checks the file exists on disk before converting;
  anything outside DATA_DIR or missing is reported and skipped (never guessed).
* After converting it re-resolves each new relative path and verifies it points
  at the same file that existed before.

Usage
-----
    python db/migrate_relative_paths.py            # dry run
    python db/migrate_relative_paths.py --yes      # apply
"""
from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import server_conn  # noqa: E402
from app.paths import DATA_DIR  # noqa: E402


def is_absolute(text: str) -> bool:
    return PureWindowsPath(text).is_absolute() or PurePosixPath(text).is_absolute()


def to_relative(path: Path) -> str | None:
    try:
        return path.resolve().relative_to(DATA_DIR.resolve()).as_posix()
    except (ValueError, OSError):
        return None


def main() -> int:
    apply = "--yes" in sys.argv[1:]
    data_root = DATA_DIR.resolve()
    print(f"DATA_DIR = {DATA_DIR}")
    print(f"Mode     = {'APPLY' if apply else 'DRY RUN (add --yes to write)'}\n")

    with server_conn() as conn:
        contracts = conn.execute(
            "SELECT id, contract_no, source_path FROM contracts "
            "WHERE source_path IS NOT NULL ORDER BY id").fetchall()
        reports = conn.execute(
            "SELECT id, contract_no, report_path FROM contract_reports "
            "WHERE report_path IS NOT NULL ORDER BY id").fetchall()

    plan: list[tuple[str, int, str, Path, str]] = []  # (table, id, col, real_file, new_rel)
    problems: list[str] = []

    def consider(table: str, rid: int, col: str, stored: str) -> None:
        if not stored:
            return
        if not is_absolute(stored):
            return  # already relative -> skip
        real = Path(stored)
        if not real.exists():
            problems.append(f"[{table}.{col} id={rid}] FILE MISSING: {stored}")
            return
        rel = to_relative(real)
        if rel is None:
            problems.append(f"[{table}.{col} id={rid}] OUTSIDE DATA_DIR: {stored}")
            return
        # round-trip check: new relative path must resolve back to the same file
        if (DATA_DIR / rel).resolve() != real.resolve():
            problems.append(f"[{table}.{col} id={rid}] ROUND-TRIP MISMATCH: {stored} -> {rel}")
            return
        plan.append((table, rid, col, real, rel))

    for rid, no, src in contracts:
        consider("contracts", rid, "source_path", src)
    for rid, no, rp in reports:
        consider("contract_reports", rid, "report_path", rp)

    if not plan and not problems:
        print("Nothing to migrate: all stored paths are already relative.")
        return 0

    print(f"{'table.column':<32} {'id':>6}  {'-> relative path'}")
    print("-" * 90)
    for table, rid, col, _real, rel in plan:
        print(f"{table + '.' + col:<32} {rid:>6}  {rel}")
    if problems:
        print("\n!! SKIPPED (need manual attention):")
        for p in problems:
            print("  " + p)

    print(f"\nPlan: {len(plan)} row(s) to convert; {len(problems)} problem(s) skipped.")
    if not apply:
        print("\n[DRY RUN] No changes made.")
        return 0

    with server_conn() as conn:
        c_map = [r for r in plan if r[0] == "contracts"]
        r_map = [r for r in plan if r[0] == "contract_reports"]
        with conn.cursor() as cur:
            for _t, rid, _col, _real, rel in c_map:
                cur.execute("UPDATE contracts SET source_path=%s WHERE id=%s", (rel, rid))
            for _t, rid, _col, _real, rel in r_map:
                cur.execute("UPDATE contract_reports SET report_path=%s WHERE id=%s", (rel, rid))
        conn.commit()

    # verify: no absolute paths remain among the converted rows
    bad = 0
    with server_conn() as conn:
        for table, rid, col, _real, rel in plan:
            row = conn.execute(
                f"SELECT {col} FROM {table} WHERE id=%s", (rid,)).fetchone()
            if not row or row[0] != rel or not (DATA_DIR / rel).exists():
                bad += 1
                print(f"VERIFY FAIL {table}.{col} id={rid}: {row[0] if row else None}")
    print(f"\n[APPLIED] converted {len(plan)} row(s); verification failures: {bad}")
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
