"""清理合同数据（含文件），可回滚。

用途：把历次联调/回归产生的合同清掉，让库里只剩真实业务数据。

安全设计：
  * 默认 **dry-run**，只打印清单，不动任何东西。
  * 真正执行要显式 `--yes`。
  * 执行前把原件 PDF / 报告 PDF **整份备份**到 data/_purge_backup/<时间戳>/，
    清单写成 manifest.json；需要找回时按目录结构拷回即可。
  * 只删合同与其从属行（items → reports → jobs → contracts），
    **不动 users / roles / permissions / audit_logs / llm_config**。
  * `--keep 合同号` 可保留指定合同；`--users` 额外清理测试账号（默认不动）。

用法：
  python db/purge_contracts.py                       # 看看会删什么
  python db/purge_contracts.py --yes                 # 执行（先备份）
  python db/purge_contracts.py --yes --keep CT-2026XXXX
  python db/purge_contracts.py --yes --users         # 连测试账号一起清
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import server_conn  # noqa: E402
from app.paths import CONTRACTS_DIR, resolve_path  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = CONTRACTS_DIR.parent
BACKUP_ROOT = DATA / "_purge_backup"

# 历次测试脚本生成的账号前缀（--users 时才用）
TEST_ACCOUNT_LIKE = ("rbac.%", "ops.%", "regression-%", "vis.beta.%", "other.beta")


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def size_of(p: str | None) -> int:
    try:
        real = resolve_path(p)
        return real.stat().st_size if real and real.exists() else 0
    except OSError:
        return 0


def main() -> int:
    args = sys.argv[1:]
    do_it = "--yes" in args
    with_users = "--users" in args
    keep: set[str] = set()
    if "--keep" in args:
        i = args.index("--keep")
        keep = {a for a in args[i + 1:] if not a.startswith("--")}

    with server_conn() as conn:
        rows = conn.execute(
            """SELECT c.id, c.contract_no, c.status, c.report_version, c.visibility, c.created_at,
                      coalesce(u.username,'?') AS owner, c.source_path,
                      (SELECT count(*) FROM report_items ri
                        JOIN contract_reports r ON r.id = ri.report_id
                       WHERE r.contract_no = c.contract_no) AS items,
                      (SELECT count(*) FROM contract_reports r WHERE r.contract_no = c.contract_no) AS reports,
                      (SELECT string_agg(r.report_path, '|') FROM contract_reports r
                        WHERE r.contract_no = c.contract_no AND r.report_path IS NOT NULL) AS report_paths
               FROM contracts c LEFT JOIN users u ON u.uid = c.owner_id
               ORDER BY c.id""").fetchall()

    targets = [r for r in rows if r[1] not in keep]
    if not targets:
        print("没有需要清理的合同。")
        return 0

    total = 0
    print(f"{'#':>4}  {'合同号':<24} {'状态':<10} {'可见性':<8} {'上传者':<16} {'文件':>8}  {'条目':>5}")
    print("-" * 96)
    for (cid, no, status, ver, vis, created, owner, src, nitems, nrep, rpaths) in targets:
        sz = size_of(src)
        for p in (rpaths or "").split("|"):
            if p:
                sz += size_of(p)
        total += sz
        print(f"{cid:>4}  {no:<24} {status:<10} {vis:<8} {owner:<16} {human(sz):>8}  {nitems:>5}")
    print("-" * 96)
    print(f"共 {len(targets)} 条合同，文件合计 {human(total)}" + (f"；保留 {sorted(keep)}" if keep else ""))

    if not do_it:
        print("\n[dry-run] 未做任何修改。确认无误后加 --yes 执行（会先备份到 data/_purge_backup/）。")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = BACKUP_ROOT / stamp
    backup.mkdir(parents=True, exist_ok=True)
    manifest = []

    # 备份：按合同号建目录，复制原件 + 报告
    for (cid, no, status, ver, vis, created, owner, src, nitems, nrep, rpaths) in targets:
        dst = backup / no
        dst.mkdir(parents=True, exist_ok=True)
        entry = {"contract_no": no, "owner": owner, "status": status,
                 "visibility": vis, "created_at": str(created), "files": []}
        src_real = resolve_path(src)
        if src_real and src_real.exists():
            shutil.copy2(src_real, dst / "original.pdf")
            entry["files"].append("original.pdf")
        for i, p in enumerate([x for x in (rpaths or "").split("|") if x]):
            real = resolve_path(p)
            if real and real.exists():
                name = f"report_{i + 1}.pdf"
                shutil.copy2(real, dst / name)
                entry["files"].append(name)
        manifest.append(entry)
    (backup / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[backup] {len(manifest)} 份合同已备份到 {backup}")

    nos = [r[1] for r in targets]
    with server_conn() as conn:
        conn.execute(
            """DELETE FROM report_items WHERE report_id IN
                 (SELECT id FROM contract_reports WHERE contract_no = ANY(%s))""", (nos,))
        conn.execute("DELETE FROM contract_reports WHERE contract_no = ANY(%s)", (nos,))
        conn.execute("DELETE FROM analysis_jobs WHERE contract_no = ANY(%s)", (nos,))
        conn.execute("DELETE FROM contracts WHERE contract_no = ANY(%s)", (nos,))
        left = conn.execute("SELECT count(*) FROM contracts").fetchone()[0]
    print(f"[db] 已删除 {len(nos)} 条合同及其条目/报告/任务；剩余合同 {left} 条")

    removed = 0
    for no in nos:
        d = DATA / "contracts" / no
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    print(f"[fs] 已删除 {removed} 个合同目录（备份保留在 {backup}）")

    if with_users:
        with server_conn() as conn:
            cond = " OR ".join(["username LIKE %s"] * len(TEST_ACCOUNT_LIKE))
            users = conn.execute(
                f"SELECT uid, username FROM users WHERE {cond} AND username <> ALL(%s)",
                (*TEST_ACCOUNT_LIKE, ["admin", "member.alpha"])).fetchall()
            for uid, uname in users:
                conn.execute("DELETE FROM user_roles WHERE uid=%s", (uid,))
                conn.execute("DELETE FROM users WHERE uid=%s", (uid,))
            print(f"[users] 已删除 {len(users)} 个测试账号")

    print("\n完成。回滚方式：把备份目录里的 original.pdf / report_*.pdf 拷回 data/contracts/<合同号>/，"
          "再按 manifest.json 重建记录（或直接重跑一次导入）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
