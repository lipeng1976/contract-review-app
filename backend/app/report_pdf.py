"""Export a manually edited report as PDF, reusing the skill's renderer.

The worker keeps the original model JSON next to every generated PDF. We load
that model (so the export keeps the exact original layout, cards, follow-up
tables, notes and footer), overwrite only the checklist cells edited in the app,
and mark those cells with ``_edited`` so the renderer makes them italic + deep
blue while keeping the same font and size.

If the original model JSON is missing (older or handwritten reports), an
equivalent model is rebuilt from the database so the export still works.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from psycopg.rows import dict_row

from app.db import CFG, server_conn
from app.paths import REPORTS_DIR, resolve_path

ROOT = Path(__file__).resolve().parent.parent
SKILL_BUILD_RAW = (CFG.get("SKILL_BUILD_SCRIPT") or "").strip()
# Path("") == Path(".") which exists -> would silently render the wrong thing.
SKILL_BUILD = Path(SKILL_BUILD_RAW) if SKILL_BUILD_RAW else None
if SKILL_BUILD is None or not SKILL_BUILD.is_file():
    SKILL_BUILD = ROOT / "skills" / "property-contract-review-report" / "scripts" / "build_review_report.py"

ITEM_ORDER_SQL = ("ri.section, coalesce(nullif(regexp_replace(ri.code,'[^0-9]','','g'),'')::numeric, 9999), ri.code")
# Fields that actually have a cell in the generated PDF.
PDF_FIELDS = {"code", "item", "provided", "verified", "contents", "notes"}
ALL_NEW_FIELDS = ["item", "provided", "verified", "contents", "notes"]


class ExportError(RuntimeError):
    pass


def _load_model(report_path: str | None, version: int, contract_no: str) -> dict | None:
    candidates: list[Path] = []
    if report_path:
        stored = resolve_path(report_path)
        p = stored if stored else None
        if p:
            candidates += [p.parent / "model.json", p.with_name("model.json")]
    candidates.append(REPORTS_DIR / contract_no / f"v{version}" / "model.json")
    for p in candidates:
        if not p.exists():
            continue
        try:
            model = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(model, dict) and model.get("sections"):
                return model
        except (OSError, ValueError):
            continue
    return None


def _fallback_model(contract: dict, report: dict, items: list[dict]) -> dict:
    sections: list[dict] = []
    for sec in sorted({it["section"] for it in items}):
        rows = []
        for it in items:
            if it["section"] != sec:
                continue
            rows.append({
                "code": it["code"], "item": it["item"],
                "provided": it["provided"], "verified": it["verified"],
                "contents": [str(x) for x in (it["contents_json"] or [])],
                "notes": it["notes"] or "",
                "_edited": {k: True for k in (it["edited_fields"] or []) if k in PDF_FIELDS},
            })
        title = next((it["section_title"] for it in items if it["section"] == sec), f"{sec}. SECTION")
        sections.append({"heading": title, "rows": rows})

    overall = report["overall_status"] or "Checked"
    enum = "FollowUp" if "follow" in str(overall).lower() else (
        "Checked" if overall in ("Checked", "Present") else "Unclear")
    cols = ["#", "Item", "Findings", "Actions", "Pages"]
    created = report["created_at"]
    date_text = created.strftime("%d %B %Y") if isinstance(created, datetime) else str(created or "")
    return {
        "lang": "en",
        "meta": {"address": contract["property_address"] or contract["address"] or contract["contract_no"],
                 "assessment_date": (date_text + "  |  edited export").strip()},
        "cards": {
            "left": {"title": "Overall review status", "badge_enum": enum, "badge_label": overall,
                     "notes": ["Provided = document availability.", "Verified = content review result."]},
            "right": {"title": "Status guide", "provided_title": "Provided",
                      "provided": ["Present", "Missing", "Unclear", "Awaiting", "NA"],
                      "verified_title": "Verified",
                      "verified": ["Checked", "Action", "Unclear", "NA"]},
        },
        "sections": sections,
        "followup": {"title": "Follow-up matters", "subheading": "", "columns": cols,
                     "rows": report["followups_json"] or []},
        "other": {"subheading": "Other matters", "columns": cols, "rows": report["other_json"] or []},
        "bullets_heading": "Other issues", "bullets": report["bullets_json"] or [],
        "note_box": report["note_box"] or None,
        "footer": "Template v1.0 - auto-generated - not legal advice",
    }


def _patch_model(model: dict, contract: dict, items: list[dict]) -> dict:
    by_code = {it["code"]: it for it in items}
    seen: set[str] = set()
    for sec in model.get("sections", []):
        if not isinstance(sec, dict):
            continue
        for row in sec.get("rows", []):
            it = by_code.get(row.get("code"))
            if it is None:
                continue
            seen.add(it["code"])
            row["provided"] = it["provided"]
            row["verified"] = it["verified"]
            row["contents"] = [str(x) for x in (it["contents_json"] or [])]
            row["notes"] = it["notes"] or ""
            row["_edited"] = {k: True for k in (it["edited_fields"] or []) if k in PDF_FIELDS}

    # Rows added in the app don't exist in the stored model — append them.
    sec_by_letter = {}
    for sec in model.get("sections", []):
        if isinstance(sec, dict) and sec.get("heading"):
            sec_by_letter[str(sec["heading"])[:1].upper()] = sec
    for it in items:
        if it["code"] in seen:
            continue
        sec = sec_by_letter.get((it["section"] or "").upper())
        if sec is None:
            sec = {"heading": it["section_title"] or f"{it['section']}. SECTION", "rows": []}
            model.setdefault("sections", []).append(sec)
            sec_by_letter[(it["section"] or "").upper()] = sec
        sec.setdefault("rows", []).append({
            "code": it["code"], "item": it["item"],
            "provided": it["provided"], "verified": it["verified"],
            "contents": [str(x) for x in (it["contents_json"] or [])],
            "notes": it["notes"] or "",
            "_edited": {k: True for k in ALL_NEW_FIELDS},
        })

    meta = model.setdefault("meta", {})
    meta["address"] = contract["property_address"] or contract["address"] or meta.get("address") or contract["contract_no"]
    return model


def build(contract_no: str, out_path: Path | None = None) -> Path:
    with server_conn() as conn:
        cur = conn.cursor(row_factory=dict_row)
        contract = cur.execute(
            "SELECT contract_no,address,property_address,status FROM contracts WHERE contract_no=%s",
            (contract_no,)).fetchone()
        if contract is None:
            raise ExportError("Contract not found")
        report = cur.execute(
            """SELECT id,version,report_path,overall_status,created_at,
                      followups_json,other_json,bullets_json,note_box,edited_at,edit_count
                 FROM contract_reports WHERE contract_no=%s ORDER BY version DESC LIMIT 1""",
            (contract_no,)).fetchone()
        if report is None:
            raise ExportError("Report not generated yet")
        if not report["edited_at"]:
            raise ExportError("Report has not been edited")
        items = cur.execute(
            f"""SELECT ri.code, ri.item, ri.section, ri.section_title, ri.provided, ri.verified,
                       ri.contents_json, ri.notes, ri.edited_fields
                FROM report_items ri JOIN contract_reports r ON r.id=ri.report_id
                WHERE r.contract_no=%s AND r.version=%s
                ORDER BY {ITEM_ORDER_SQL}""",
            (contract_no, report["version"])).fetchall()

    model = _load_model(report["report_path"], report["version"], contract_no)
    if model is None:
        model = _fallback_model(contract, report, items)
    model = _patch_model(model, contract, items)

    if not SKILL_BUILD.exists():
        raise ExportError(f"skill renderer not found: {SKILL_BUILD}")
    out_path = Path(out_path) if out_path else (
        REPORTS_DIR / contract_no / f"v{report['version']}" / "report_edited.pdf")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fd, model_tmp = tempfile.mkstemp(prefix="edited_model_", suffix=".json", dir=str(out_path.parent))
    os.close(fd)
    fd, pdf_tmp = tempfile.mkstemp(prefix="edited_report_", suffix=".pdf", dir=str(out_path.parent))
    os.close(fd)
    try:
        Path(model_tmp).write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
        cmd = [sys.executable, str(SKILL_BUILD), "--model", model_tmp,
               "--out", pdf_tmp, "--lang", str(model.get("lang") or "en")]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if proc.returncode != 0 or not Path(pdf_tmp).exists():
            raise ExportError("renderer failed: " + (proc.stderr or proc.stdout)[-500:])
        os.replace(pdf_tmp, out_path)
    finally:
        for p in (model_tmp, pdf_tmp):
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass
    return out_path
