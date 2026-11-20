"""把 skill 产出的 model JSON 按 A-G 分区入库，并用真实报告做回归验证。

用法：
    python db/ingest_report.py <model.json> <contract_no> [--verify-only]

Worker 阶段直接调用 ingest_report() 即可；本文件的 __main__ 是回归测试入口。
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parent.parent
SECTION_CODES = tuple("ABCDEFG")


def load_pg_config() -> dict:
    cfg = {}
    for line in (ROOT.parent / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return {
        "host": cfg.get("PGHOST", "127.0.0.1"),
        "port": int(cfg.get("PGPORT", "5434")),
        "user": cfg.get("PGUSER", "postgres"),
        "password": cfg.get("PGPASSWORD", ""),
        "dbname": cfg.get("PGDATABASE", "contract_review"),
    }


TAG_RE = re.compile(r"<[^>]+>")
PAGE_RE = re.compile(r"\bpp?\.?\s*(\d+)(?:\s*[-–—]\s*(\d+))?", re.I)


def clean(value: str) -> str:
    return html.unescape(TAG_RE.sub("", value or "")).strip()


def extract_pages(text: str) -> list[str]:
    """把 (pp.46-48) / p.8 之类展开成 ['p.8', 'p.46', 'p.47', 'p.48']。"""
    pages: list[str] = []
    for m in PAGE_RE.finditer(text or ""):
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else start
        for n in range(start, min(end, start + 60) + 1):
            token = f"p.{n}"
            if token not in pages:
                pages.append(token)
    return pages


MONEY_RE = r"([\d][\d,]*(?:\.\d{2})?)"


def lift_scalars(sections: list[dict]) -> dict:
    """从 A/C 节内容抽取可检索标量；抽不到留 NULL，不臆造。"""
    rows = {r["code"]: " ".join(clean(c) for c in r["contents"])
            for s in sections for r in s["rows"]}
    out: dict[str, object] = {}

    text = rows.get("A1", "")
    m = re.search(r"Property:\s*MATCH\s*-\s*(.+?)\s+in the contract", text, re.I)
    if m:
        out["property_address"] = m.group(1).strip()

    text = rows.get("A2", "")
    m = re.search(r"Volume\s+(\d+)\s+Folio\s+(\d+)", text, re.I)
    if m:
        out["title_volume"], out["title_folio"] = m.group(1), m.group(2)
    m = re.search(r"Lot\s+(\d+)\s+on\s+Plan of Subdivision\s+(\d+)", text, re.I)
    if m:
        out["lot_number"], out["plan_number"] = m.group(1), m.group(2)

    text = rows.get("A3", "")
    m = re.search(r"Registered proprietor:\s*([^,;]+)", text, re.I)
    if m:
        out["registered_owner"] = m.group(1).strip()
    m = re.search(r"Vendor:\s*([A-Z][A-Za-z .'-]+?)(?:,|\s+as|\s+\()", text)
    if m:
        out["vendor_name"] = m.group(1).strip()

    text = rows.get("A5", "")
    m = re.search(r"Area:\s*([\d.]+)\s*sq", text, re.I)
    if m:
        out["land_area_sqm"] = m.group(1)
    m = re.search(r"perimeter:\s*([\d.]+)\s*m", text, re.I)
    if m:
        out["perimeter_m"] = m.group(1)

    text = rows.get("C1", "")
    m = re.search(r"council\s*\$" + MONEY_RE, text, re.I)
    if m:
        out["council_rates_amt"] = m.group(1).replace(",", "")
    m = re.search(r"water/sewer\s*\$" + MONEY_RE, text, re.I)
    if m:
        out["water_charges_amt"] = m.group(1).replace(",", "")
    return out


def _data_dir() -> Path:
    """Resolve CONTRACT_DATA_DIR from .env so CLI replay stores relative paths."""
    raw = ""
    env = ROOT.parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("CONTRACT_DATA_DIR="):
                raw = line.split("=", 1)[1].strip()
                break
    return Path(raw) if raw else (ROOT / "data")


def _normalize_report_path(value: str | None) -> str | None:
    """Store only relative paths. Accept legacy absolute paths on input."""
    if not value:
        return value
    from pathlib import PurePosixPath, PureWindowsPath
    absolute = PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute()
    if not absolute:
        return PurePosixPath(value).as_posix()
    try:
        return Path(value).resolve().relative_to(_data_dir().resolve()).as_posix()
    except (ValueError, OSError):
        return value  # outside DATA_DIR: keep as-is rather than corrupt it


def ingest_report(conn, model: dict, contract_no: str, report_path: str | None = None,
                  report_sha256: str | None = None) -> int:
    """入库一份报告，返回写入的 report_items 行数。"""
    report_path = _normalize_report_path(report_path)
    scalars = lift_scalars(model["sections"])
    overall = model.get("cards", {}).get("left", {}).get("badge_label")
    address = model.get("meta", {}).get("address") or scalars.get("property_address")

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM contracts WHERE contract_no = %s", (contract_no,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"contract {contract_no} 不存在，先建合同行")
        contract_id = row[0]

        version = 1
        cur.execute("SELECT coalesce(max(version),0)+1 FROM contract_reports WHERE contract_no=%s",
                    (contract_no,))
        version = cur.fetchone()[0]

        cur.execute(
            """INSERT INTO contract_reports
               (contract_no, version, overall_status, followups_json, other_json,
                bullets_json, note_box, report_path, report_sha256)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (contract_no, version, overall,
             Jsonb(model.get("followup", {}).get("rows", [])),
             Jsonb(model.get("other", {}).get("rows", [])),
             Jsonb(model.get("bullets", [])),
             Jsonb(model.get("note_box", {})), report_path, report_sha256),
        )
        report_id = cur.fetchone()[0]

        count = 0
        for section in model["sections"]:
            code = clean(section["heading"])[:1].upper()
            assert code in SECTION_CODES, f"未知章节 {code}"
            title = clean(section["heading"])
            for r in section["rows"]:
                contents = r.get("contents", [])
                text = " ".join(clean(c) for c in contents)
                cur.execute(
                    """INSERT INTO report_items
                       (report_id, section, section_title, code, item, provided, verified,
                        contents_json, contents_text, pages, notes)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (report_id, code, title, r["code"], r["item"],
                     r.get("provided"), r.get("verified"),
                     Jsonb(contents), text, extract_pages(text), r.get("notes")),
                )
                count += 1

        sets, vals = [], []
        for k, v in scalars.items():
            sets.append(f"{k} = %s")
            vals.append(v)
        if address:
            sets.append("address = %s")
            vals.append(address)
        sets.append("status = 'completed'")
        sets.append("report_version = %s")
        vals.append(version)
        vals.append(contract_id)
        cur.execute(f"UPDATE contracts SET {', '.join(sets)} WHERE id = %s", vals)
    return count


def verify(conn, contract_no: str) -> None:
    print("\n=== 回归验证 ===")
    with conn.cursor() as cur:
        cur.execute("""SELECT section, count(*) FROM report_items ri
                       JOIN contract_reports r ON r.id=ri.report_id
                       WHERE r.contract_no=%s GROUP BY section ORDER BY section""", (contract_no,))
        print("各分区行数:", dict(cur.fetchall()))

        cur.execute("""SELECT count(*) FROM report_items ri JOIN contract_reports r
                       ON r.id=ri.report_id WHERE r.contract_no=%s""", (contract_no,))
        print("总行数:", cur.fetchone()[0], "(期望 82)")

        for code in ("A6", "G2", "A1"):
            cur.execute("""SELECT verified, contents_text, pages FROM report_items ri
                           JOIN contract_reports r ON r.id=ri.report_id
                           WHERE r.contract_no=%s AND ri.code=%s""", (contract_no, code))
            v, text, pages = cur.fetchone()
            print(f"{code}: verified={v}, pages={pages[:6]}, text={text[:95]}")

        cur.execute("""SELECT count(*) FROM report_items ri JOIN contract_reports r
                       ON r.id=ri.report_id WHERE r.contract_no=%s
                       AND ri.verified IN ('Action','Unclear')""", (contract_no,))
        print("风险项(Action/Unclear):", cur.fetchone()[0])

        cur.execute("""SELECT ri.section, ri.code FROM report_items ri JOIN contract_reports r
                       ON r.id=ri.report_id WHERE r.contract_no=%s AND 'p.44' = ANY(ri.pages)
                       ORDER BY ri.section, ri.code LIMIT 8""", (contract_no,))
        print("p.44 支撑的结论:", cur.fetchall())

        cur.execute("""SELECT property_address, title_volume, title_folio, lot_number,
                              plan_number, registered_owner, land_area_sqm,
                              council_rates_amt, water_charges_amt, status
                       FROM contracts WHERE contract_no=%s""", (contract_no,))
        print("回填标量:", cur.fetchone())

        cur.execute("""EXPLAIN SELECT * FROM report_items WHERE section='A' AND report_id=1""")
        plan = "\n".join(r[0] for r in cur.fetchall())
        print("分区裁剪:", "report_items_a" if "report_items_a" in plan else plan[:300])


def main() -> None:
    model_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "_work/hinkler/model_v3.json"
    contract_no = sys.argv[2] if len(sys.argv) > 2 else "CT-20261118-H1NKL3"
    model = json.loads(model_path.read_text(encoding="utf-8"))

    with psycopg.connect(**load_pg_config()) as conn:
        if "--verify-only" not in sys.argv:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO users (username, password_hash, display_name, status)
                               VALUES (%s,%s,%s,'active') ON CONFLICT (username) DO NOTHING""",
                            (f"regression-{contract_no}", "NOT_A_REAL_HASH", "回归测试账号"))
                cur.execute("SELECT uid FROM users WHERE username=%s", (f"regression-{contract_no}",))
                owner = cur.fetchone()[0]
                cur.execute("""INSERT INTO contracts (contract_no, owner_id, status)
                               VALUES (%s,%s,'queued') ON CONFLICT (contract_no) DO NOTHING""",
                            (contract_no, owner))
            n = ingest_report(conn, model, contract_no)
            print(f"入库完成: {n} 行 report_items, contract_no={contract_no}")
        verify(conn, contract_no)


if __name__ == "__main__":
    main()
