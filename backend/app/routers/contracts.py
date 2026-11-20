from __future__ import annotations

import csv
import hashlib
import io
import re
import secrets
import shutil
import string
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from ..auth import (COOKIE, TOKEN_HOURS, _rbac_for, current_user, hash_password, make_token,
                   require_active, require_admin, require_perm, verify_password)
from ..db import CFG, api_session, db_status, server_conn
from ..core import (ROOT, WEB_DIR, DATA_DIR, CONTRACTS_DIR, REPORTS_DIR, TMP_DIR,
                    resolve_path, to_relative,
                    APP_NAME, APP_VERSION, PUBLIC_BASE_URL, MAX_UPLOAD,
                    WORKER_ALIVE_SECONDS, STALL_SECONDS, audit, new_contract_no,
                    ACCOUNT_RE, pick_account, valid_account, require_contract_read,
                    _ensure_contract_readable, role_by_name, assign_roles, user_roles,
                    rowdict, ITEM_ORDER_SQL, worker_status)

router = APIRouter()

@router.post("/api/contracts")
async def upload_contract(file: UploadFile = File(...), visibility: str = Form("shared"),
                          user: dict = Depends(require_perm("contract.upload"))):
    # 可见性随上传落库；默认 shared（团队可见），可改 private（仅自己）。
    if visibility not in ("shared", "private"):
        raise HTTPException(400, "visibility must be shared or private")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file")
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "File exceeds 100 MB")
    if not raw[:5] == b"%PDF-":
        raise HTTPException(400, "Only PDF files are accepted")
    digest = hashlib.sha256(raw).hexdigest()

    with api_session(user["uid"]) as conn:
        cur = conn.execute(
            "SELECT contract_no, status FROM contracts WHERE source_sha256=%s AND owner_id=%s",
            (digest, user["uid"]))
        existing = cur.fetchone()
        if existing:
            return {"ok": True, "duplicate": True, "contract_no": existing[0], "status": existing[1]}

        contract_no = new_contract_no()
        while conn.execute("SELECT 1 FROM contracts WHERE contract_no=%s", (contract_no,)).fetchone():
            contract_no = new_contract_no()
        target = CONTRACTS_DIR / contract_no
        target.mkdir(parents=True, exist_ok=True)
        (target / "original.pdf").write_bytes(raw)
        conn.execute(
            """INSERT INTO contracts (contract_no, owner_id, address, source_path, source_sha256,
                                      status, visibility)
               VALUES (%s,%s,%s,%s,%s,'queued',%s)""",
            (contract_no, user["uid"], file.filename, to_relative(target / "original.pdf"), digest, visibility))
        conn.execute(
            "INSERT INTO analysis_jobs (contract_no, attempt, status) VALUES (%s,1,'queued')",
            (contract_no,))
    audit(user["uid"], "upload", "contract", contract_no,
          {"sha256": digest, "filename": file.filename, "visibility": visibility})
    return {"ok": True, "duplicate": False, "contract_no": contract_no, "status": "queued",
            "visibility": visibility,
            "message": "Uploaded; analysis task queued"}


def _contract_list(user: dict, status: str | None, q: str | None, limit: int,
                   archived_only: bool) -> dict:
    # Archived contracts never appear in the normal query: "WHERE 1=1" below is
    # deliberately replaced by an explicit archive filter for both routes.
    inner = ("SELECT c.contract_no, c.address, c.status, c.report_version, c.created_at, "
             "c.archived_at, c.archived_by, "
             "c.property_address, c.registered_owner, c.visibility, c.owner_id, "
             "username_of(c.owner_id) AS owner_name, "
             "(c.owner_id = %s) AS is_mine, "
             "max(r.edited_at) AS report_edited_at, "
             "count(ri.id) FILTER (WHERE ri.verified IN ('Action','Unclear')) AS risk_count "
             "FROM contracts c LEFT JOIN contract_reports r "
             "ON r.contract_no=c.contract_no AND r.version=c.report_version "
             "LEFT JOIN report_items ri ON ri.report_id=r.id WHERE 1=1")
    args: list = [user["uid"]]
    inner += " AND c.archived_at IS NOT NULL" if archived_only else " AND c.archived_at IS NULL"
    if status:
        inner += " AND c.status=%s"
        args.append(status)
    if q and q.strip():
        like = "%" + q.strip()[:80] + "%"
        inner += (" AND (c.contract_no ILIKE %s OR coalesce(c.property_address,'') ILIKE %s "
                  "OR coalesce(c.address,'') ILIKE %s OR coalesce(c.registered_owner,'') ILIKE %s "
                  "OR coalesce(c.lot_number,'') ILIKE %s)")
        args += [like] * 5
    inner += " GROUP BY c.id ORDER BY c.created_at DESC LIMIT %s"
    args.append(limit)
    sql = ("SELECT x.*, jb.job_status, jb.stage, jb.stage_msg, jb.stage_at, "
           "jb.started_at AS job_started_at, jb.error_code, jb.error_msg, jb.attempt, "
           "jb.stage_age_seconds, jb.finished_at AS job_finished_at "
           "FROM (" + inner + ") x "
           "LEFT JOIN LATERAL ("
           "  SELECT j.status AS job_status, j.stage, j.stage_msg, j.stage_at, j.started_at,"
           "         j.error_code, j.error_msg, j.attempt, j.finished_at,"
           "         EXTRACT(EPOCH FROM (now()-j.stage_at))::int AS stage_age_seconds"
           "    FROM analysis_jobs j WHERE j.contract_no=x.contract_no"
           "   ORDER BY j.id DESC LIMIT 1) jb ON TRUE "
           "ORDER BY x.created_at DESC")
    with api_session(user["uid"]) as conn:
        cur = conn.execute(sql, args)
        rows = [rowdict(cur, r) for r in cur.fetchall()]
    return {"contracts": rows, "total": len(rows), "worker": worker_status()}


@router.get("/api/contracts")
def list_contracts(status: str | None = Query(None), q: str | None = Query(None),
                   limit: int = Query(50, le=200),
                   user: dict = Depends(require_perm("contract.view"))):
    return _contract_list(user, status, q, limit, archived_only=False)


@router.get("/api/contracts/archived")
def list_archived_contracts(status: str | None = Query(None), q: str | None = Query(None),
                            limit: int = Query(50, le=200),
                            user: dict = Depends(require_perm("contract.archive_view"))):
    """Separate archive query. Archived rows are invisible on the normal route."""
    return _contract_list(user, status, q, limit, archived_only=True)


@router.get("/api/contracts/{contract_no}")
def contract_detail(contract_no: str, user: dict = Depends(require_contract_read)):
    with api_session(user["uid"]) as conn:
        cur = conn.execute(
            """SELECT contract_no, address, property_address, status, report_version,
                      archived_at, archived_by,
                      title_volume, title_folio, lot_number, plan_number, registered_owner,
                      land_area_sqm, council_rates_amt, water_charges_amt, created_at,
                      visibility, owner_id, username_of(owner_id) AS owner_name,
                      (owner_id = %s) AS is_mine
               FROM contracts WHERE contract_no=%s""", (user["uid"], contract_no))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "Contract not found")
        _ensure_contract_readable(user, row[5] is not None)
        detail = rowdict(cur, row)
        cur = conn.execute(
            """SELECT r.id, r.version, r.overall_status, r.created_at,
                      r.edited_at, r.edit_count, (r.edited_at IS NOT NULL) AS is_edited,
                      coalesce(r.followups_json,'[]'::jsonb) AS followups,
                      jsonb_array_length(coalesce(r.followups_json,'[]'::jsonb)) AS followup_count,
                      (SELECT count(*) FROM report_items ri
                        WHERE ri.report_id=r.id AND ri.verified IN ('Action','Unclear')) AS risk_count
               FROM contract_reports r WHERE r.contract_no=%s ORDER BY r.version DESC LIMIT 1""", (contract_no,))
        report = rowdict(cur, cur.fetchone()) if cur.rowcount else None
        sections = []
        if report:
            cur = conn.execute(
                """SELECT section, section_title, count(*) AS n,
                          count(*) FILTER (WHERE verified IN ('Action','Unclear')) AS risks
                   FROM report_items WHERE report_id=%s GROUP BY section, section_title ORDER BY section""",
                (report["id"],))
            sections = [rowdict(cur, r) for r in cur.fetchall()]
        jobs = []
        cur = conn.execute(
            """SELECT id, attempt, status, error_code, error_msg, stage, stage_msg, stage_at,
                      started_at, finished_at,
                      EXTRACT(EPOCH FROM (now()-stage_at))::int AS stage_age_seconds
               FROM analysis_jobs WHERE contract_no=%s ORDER BY id DESC LIMIT 5""",
            (contract_no,))
        jobs = [rowdict(cur, r) for r in cur.fetchall()]
    return {"contract": detail, "report": report, "sections": sections, "jobs": jobs,
            "worker": worker_status(), "stall_seconds": STALL_SECONDS}


@router.put("/api/contracts/{contract_no}/visibility")
def set_contract_visibility(contract_no: str, payload: dict,
                            user: dict = Depends(require_contract_read)):
    """共享 / 仅自己可见。随时可改，只有上传者（或超管）能改。"""
    vis = str(payload.get("visibility") or "").strip().lower()
    if vis not in ("shared", "private"):
        raise HTTPException(400, "visibility must be shared or private")
    _require_edit_scope(user, contract_no)          # 404 若看不见；403 若不是本人
    with api_session(user["uid"]) as conn:
        old = conn.execute("SELECT visibility FROM contracts WHERE contract_no=%s",
                           (contract_no,)).fetchone()
        conn.execute("UPDATE contracts SET visibility=%s WHERE contract_no=%s", (vis, contract_no))
    audit(user["uid"], "set_visibility", "contract", contract_no,
          {"from": old[0] if old else None, "to": vis})
    return {"ok": True, "contract_no": contract_no, "visibility": vis}


@router.post("/api/contracts/{contract_no}/archive")
def archive_contract(contract_no: str, payload: dict | None = None,
                     user: dict = Depends(require_perm("contract.archive"))):
    """Archive a contract, or restore it with {"restore": true}.

    Archived contracts disappear from the normal query and become read-only.
    Only the owner or an administrator may change the flag.
    """
    _require_owner_scope(user, contract_no)          # 404 / 403 before any write
    restore = bool((payload or {}).get("restore"))
    with server_conn() as conn:
        row = conn.execute("SELECT archived_at FROM contracts WHERE contract_no=%s",
                           (contract_no,)).fetchone()
        if row is None:
            raise HTTPException(404, "Contract not found")
        if restore and row[0] is None:
            raise HTTPException(409, "Contract is not archived")
        if not restore and row[0] is not None:
            raise HTTPException(409, "Contract is already archived")
        if restore:
            conn.execute("UPDATE contracts SET archived_at=NULL, archived_by=NULL "
                         "WHERE contract_no=%s", (contract_no,))
        else:
            conn.execute("UPDATE contracts SET archived_at=now(), archived_by=%s "
                         "WHERE contract_no=%s", (user["uid"], contract_no))
    audit(user["uid"], "restore_contract" if restore else "archive_contract",
          "contract", contract_no, {"restore": restore})
    return {"ok": True, "contract_no": contract_no, "archived": not restore}


@router.get("/api/contracts/{contract_no}/items")
def contract_items(contract_no: str, section: str | None = Query(None),
                   verified: str | None = Query(None), q: str | None = Query(None),
                   user: dict = Depends(require_contract_read)):
    if section is not None and section.upper() not in "ABCDEFG":
        raise HTTPException(400, "section must be one of A-G")
    # Pin to the newest report version: re-analysis creates a new report, and
    # without this filter every earlier version's rows would show up again.
    sql = """SELECT ri.id, ri.section, ri.code, ri.item, ri.provided, ri.verified,
                    ri.contents_json, ri.contents_text, ri.pages, ri.notes,
                    ri.edited_fields, ri.edited_at
             FROM report_items ri JOIN contract_reports r ON r.id=ri.report_id
             WHERE r.contract_no=%s
               AND r.version=(SELECT max(version) FROM contract_reports WHERE contract_no=%s)"""
    args: list = [contract_no, contract_no]
    if section:
        sql += " AND ri.section=%s"
        args.append(section.upper())
    if verified:
        sql += " AND ri.verified=%s"
        args.append(verified)
    if q and q.strip():
        # Whole-contract search: code, item title and findings body.
        like = "%" + q.strip()[:80] + "%"
        sql += (" AND (ri.code ILIKE %s OR ri.item ILIKE %s "
                "OR coalesce(ri.contents_text,'') ILIKE %s)")
        args += [like, like, like]
    sql += f" ORDER BY {ITEM_ORDER_SQL}"
    with api_session(user["uid"]) as conn:
        # RLS 会静默过滤不可见的行，这里显式判一次，让"别人的私有合同"回到 404，
        # 而不是返回 200 + 空清单（前端会误显示成"没有条目"）。
        crow = conn.execute("SELECT archived_at FROM contracts WHERE contract_no=%s",
                            (contract_no,)).fetchone()
        if crow is None:
            raise HTTPException(404, "Contract not found")
        _ensure_contract_readable(user, crow[0] is not None)
        cur = conn.execute(sql, args)
        return {"total": cur.rowcount, "items": [rowdict(cur, r) for r in cur.fetchall()]}


# ---------- contract data editing (contract.edit) ----------
# Only these contract columns can be edited from the app; contract_no / owner /
# hashes / status are system-managed. `item`/`code`/`section` on checklist rows
# are immutable by design: admins may add rows and refine their content, but the
# 82-row statutory skeleton itself must never be silently rewritten.
CONTRACT_EDITABLE = {
    "address": "text", "property_address": "text", "title_volume": "text",
    "title_folio": "text", "lot_number": "text", "plan_number": "text",
    "registered_owner": "text", "land_area_sqm": "num",
    "council_rates_amt": "num", "water_charges_amt": "num",
}
PROVIDED_ENUM = ("Present", "Missing", "Unclear", "Awaiting", "NA")
VERIFIED_ENUM = ("Checked", "Action", "Unclear", "NA")
SECTION_TITLES = {
    "A": "A. PROPERTY & TITLE INFORMATION", "B": "B. PLANNING, ZONING & GOVERNMENT",
    "C": "C. RATES, CHARGES & OUTGOINGS", "D": "D. BUILDING, CONSTRUCTION & CONDITION",
    "E": "E. LEASES, OCCUPANCY & THIRD PARTY INTERESTS",
    "F": "F. RISKS, LITIGATION & SPECIAL MATTERS",
    "G": "G. REQUIRED DOCUMENTS & ATTACHMENTS",
}


# contract-read gate helpers live in the RBAC helpers section near the top of
# this module: route decorators evaluate Depends(require_contract_read) at
# import time, so the name must exist before the first @router.get below.
def _require_owner_scope(user: dict, contract_no: str) -> None:
    with api_session(user["uid"]) as conn:
        row = conn.execute("SELECT owner_id FROM contracts WHERE contract_no=%s",
                           (contract_no,)).fetchone()
    if row is None:
        raise HTTPException(404, "Contract not found")
    if row[0] != user["uid"] and not user.get("is_superuser"):
        raise HTTPException(403, "Only the owner or an administrator can manage this contract")


def _require_edit_scope(user: dict, contract_no: str) -> None:
    """Owner or superuser only, and archived contracts are strictly read-only."""
    _require_owner_scope(user, contract_no)
    with server_conn() as conn:
        row = conn.execute("SELECT archived_at FROM contracts WHERE contract_no=%s",
                           (contract_no,)).fetchone()
    if row and row[0] is not None:
        raise HTTPException(409, "Archived contracts are read-only; restore them first")


def _latest_report_id(conn, contract_no: str) -> int:
    row = conn.execute(
        "SELECT id FROM contract_reports WHERE contract_no=%s ORDER BY version DESC LIMIT 1",
        (contract_no,)).fetchone()
    if row is None:
        raise HTTPException(409, "Report not generated yet")
    return row[0]


def _mark_report_edited(conn, contract_no: str) -> None:
    """Flag the newest report as manually edited (and block blind re-analysis)."""
    conn.execute(
        """UPDATE contract_reports SET edited_at=now(), edit_count=edit_count+1
            WHERE id=(SELECT id FROM contract_reports WHERE contract_no=%s
                      ORDER BY version DESC LIMIT 1)""", (contract_no,))


def _as_text_lines(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = [str(v) for v in value]
    else:
        items = str(value).splitlines()
    return [v.strip() for v in items if v and v.strip()]


@router.put("/api/contracts/{contract_no}")
def update_contract(contract_no: str, payload: dict, user: dict = Depends(require_perm("contract.edit"))):
    _require_edit_scope(user, contract_no)
    with server_conn() as conn:
        old_row = conn.execute(
            "SELECT " + ", ".join(CONTRACT_EDITABLE) + " FROM contracts WHERE contract_no=%s",
            (contract_no,)).fetchone()
        old = dict(zip(CONTRACT_EDITABLE, old_row or ()))
        sets: list[str] = []
        vals: list = []
        changed: dict = {}
        for key, kind in CONTRACT_EDITABLE.items():
            if key not in payload:
                continue
            raw = payload[key]
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                val = None
            elif kind == "num":
                try:
                    val = float(str(raw).replace(",", "").replace("$", "").strip())
                except ValueError:
                    raise HTTPException(400, f"{key} must be a number")
            else:
                val = str(raw).strip()[:200]
            prev = old.get(key)
            if kind == "num" and prev is not None and val is not None:
                prev = float(prev)
            if val == prev:
                continue
            sets.append(f"{key}=%s")
            vals.append(val)
            changed[key] = val
        if not sets:
            return {"ok": True, "contract_no": contract_no, "updated": [], "unchanged": True}
        vals.append(contract_no)
        conn.execute(f"UPDATE contracts SET {', '.join(sets)} WHERE contract_no=%s", vals)
        _mark_report_edited(conn, contract_no)
    audit(user["uid"], "edit_contract", "contract", contract_no, {"fields": changed})
    return {"ok": True, "contract_no": contract_no, "updated": list(changed)}


@router.post("/api/contracts/{contract_no}/items")
def add_report_item(contract_no: str, payload: dict, user: dict = Depends(require_perm("contract.edit"))):
    _require_edit_scope(user, contract_no)
    section = str(payload.get("section", "")).strip().upper()[:1]
    if section not in "ABCDEFG":
        raise HTTPException(400, "section must be one of A-G")
    item_text = str(payload.get("item", "")).strip()[:300]
    if not item_text:
        raise HTTPException(400, "item text is required")
    provided = str(payload.get("provided") or "Present").strip()
    verified = str(payload.get("verified") or "Unclear").strip()
    if provided not in PROVIDED_ENUM:
        raise HTTPException(400, f"provided must be one of {', '.join(PROVIDED_ENUM)}")
    if verified not in VERIFIED_ENUM:
        raise HTTPException(400, f"verified must be one of {', '.join(VERIFIED_ENUM)}")
    lines = _as_text_lines(payload.get("contents"))
    text = " ".join(lines)
    from db.ingest_report import extract_pages
    pages = [str(p) for p in payload.get("pages") or []] or extract_pages(text)
    notes = str(payload.get("notes") or "").strip()[:500] or None

    with server_conn() as conn:
        report_id = _latest_report_id(conn, contract_no)
        code = str(payload.get("code") or "").strip().upper()
        if code:
            if not re.fullmatch(r"[A-G][0-9A-Z\-]{1,9}", code) or not code.startswith(section):
                raise HTTPException(400, f"code must look like {section}12 and match the section")
            if conn.execute("SELECT 1 FROM report_items WHERE report_id=%s AND code=%s",
                            (report_id, code)).fetchone():
                raise HTTPException(409, "Item code already exists in this report")
        else:
            used = [r[0] for r in conn.execute(
                "SELECT code FROM report_items WHERE report_id=%s AND section=%s",
                (report_id, section)).fetchall()]
            nums = [int(m.group(1)) for c in used if (m := re.fullmatch(section + r"(\d+)", c))]
            code = f"{section}{(max(nums) if nums else 0) + 1}"
        title_row = conn.execute(
            "SELECT section_title FROM report_items WHERE report_id=%s AND section=%s LIMIT 1",
            (report_id, section)).fetchone()
        title = title_row[0] if title_row else SECTION_TITLES[section]
        new_id = conn.execute(
            """INSERT INTO report_items
               (report_id, section, section_title, code, item, provided, verified,
                contents_json, contents_text, pages, notes, edited_fields, edited_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now()) RETURNING id""",
            (report_id, section, title, code, item_text, provided, verified,
             Jsonb(lines), text, pages, notes,
             ["item", "provided", "verified", "contents", "notes"])).fetchone()[0]
        _mark_report_edited(conn, contract_no)
    audit(user["uid"], "add_item", "contract", contract_no,
          {"code": code, "section": section, "item": item_text[:80]})
    return {"ok": True, "id": new_id, "code": code, "section": section, "report_id": report_id}


@router.put("/api/contracts/{contract_no}/items/{item_id}")
def update_report_item(contract_no: str, item_id: int, payload: dict,
                       user: dict = Depends(require_perm("contract.edit"))):
    _require_edit_scope(user, contract_no)
    # The checklist identity is immutable: reject any attempt to rewrite it.
    for immutable in ("item", "code", "section", "section_title"):
        if immutable in payload:
            raise HTTPException(400, f"{immutable} cannot be modified; add a new item instead")
    with server_conn() as conn:
        row = conn.execute(
            """SELECT ri.provided, ri.verified, ri.contents_json, ri.notes, ri.pages
               FROM report_items ri JOIN contract_reports r ON r.id=ri.report_id
               WHERE ri.id=%s AND r.contract_no=%s""", (item_id, contract_no)).fetchone()
        if row is None:
            raise HTTPException(404, "Item not found in this contract")
        old = {"provided": row[0], "verified": row[1], "contents": list(row[2] or []),
               "notes": row[3] or "", "pages": list(row[4] or [])}
        sets: list[str] = []
        vals: list = []
        changed: dict = {}
        if "provided" in payload:
            provided = str(payload["provided"] or "").strip()
            if provided not in PROVIDED_ENUM:
                raise HTTPException(400, f"provided must be one of {', '.join(PROVIDED_ENUM)}")
            if provided != old["provided"]:
                sets.append("provided=%s"); vals.append(provided); changed["provided"] = provided
        if "verified" in payload:
            verified = str(payload["verified"] or "").strip()
            if verified not in VERIFIED_ENUM:
                raise HTTPException(400, f"verified must be one of {', '.join(VERIFIED_ENUM)}")
            if verified != old["verified"]:
                sets.append("verified=%s"); vals.append(verified); changed["verified"] = verified
        if "contents" in payload:
            lines = _as_text_lines(payload["contents"])
            if lines != old["contents"]:
                text = " ".join(lines)
                sets += ["contents_json=%s", "contents_text=%s"]
                vals += [Jsonb(lines), text]
                changed["contents"] = len(lines)
        if "notes" in payload:
            notes = str(payload["notes"] or "").strip()[:500] or None
            if (notes or "") != old["notes"]:
                sets.append("notes=%s"); vals.append(notes); changed["notes"] = notes
        if "pages" in payload:
            pages = [str(p).strip() for p in (payload["pages"] or []) if str(p).strip()]
            if pages != old["pages"]:
                sets.append("pages=%s"); vals.append(pages); changed["pages"] = len(pages)
        if not sets:
            # Opening the edit dialog and pressing Save without changing anything
            # must NOT mark the report as edited or block re-analysis.
            return {"ok": True, "id": item_id, "updated": [], "unchanged": True}
        sets.append("edited_fields = ARRAY(SELECT DISTINCT unnest(coalesce(edited_fields,'{}'::text[]) || %s::text[]))")
        vals.append(list(changed))
        sets.append("edited_at = now()")
        vals.append(item_id)
        conn.execute(f"UPDATE report_items SET {', '.join(sets)} WHERE id=%s", vals)
        _mark_report_edited(conn, contract_no)
    audit(user["uid"], "edit_item", "contract", contract_no, {"item_id": item_id, "fields": changed})
    return {"ok": True, "id": item_id, "updated": list(changed)}


@router.get("/api/contracts/{contract_no}/audit")
def contract_audit(contract_no: str, limit: int = Query(50, le=200),
                   user: dict = Depends(require_perm("contract.edit"))):
    """Edit history for one contract (who changed what, when)."""
    _require_edit_scope(user, contract_no)
    with server_conn() as conn:
        rows = conn.execute(
            """SELECT a.id, a.action, a.detail, a.created_at,
                      coalesce(u.display_name, u.username, 'system')
               FROM audit_logs a LEFT JOIN users u ON u.uid=a.actor_uid
               WHERE a.target_type='contract' AND a.target_id=%s
               ORDER BY a.id DESC LIMIT %s""", (contract_no, limit)).fetchall()
    return {"logs": [{"id": r[0], "action": r[1], "detail": r[2],
                      "created_at": r[3].isoformat() if r[3] else None,
                      "actor": r[4]} for r in rows]}



@router.get("/api/contracts/{contract_no}/export")
def export_contract(contract_no: str, what: str = Query("items"),
                    user: dict = Depends(require_perm("contract.download"))):
    """CSV export of the checklist or the follow-up list (UTF-8 BOM for Excel)."""
    if what not in ("items", "followups"):
        raise HTTPException(400, "what must be items or followups")
    with api_session(user["uid"]) as conn:
        crow = conn.execute(
            "SELECT property_address, address, registered_owner, report_version, archived_at "
            "FROM contracts WHERE contract_no=%s", (contract_no,)).fetchone()
    if crow is None:
        raise HTTPException(404, "Contract not found")
    _ensure_contract_readable(user, crow[4] is not None)
    if not crow[3]:
        raise HTTPException(409, "Report not generated yet")
    header = ["Contract", contract_no, "Address", crow[0] or crow[1] or "",
              "Registered owner", crow[2] or "", "Report version", crow[3]]
    buf = io.StringIO()
    w = csv.writer(buf)
    if what == "items":
        with api_session(user["uid"]) as conn:
            rows = conn.execute(
                """SELECT ri.section, ri.section_title, ri.code, ri.item, ri.provided,
                          ri.verified, ri.contents_text, ri.pages, ri.notes
                   FROM report_items ri JOIN contract_reports r ON r.id=ri.report_id
                   WHERE r.contract_no=%s
                     AND r.version=(SELECT max(version) FROM contract_reports WHERE r.contract_no=%s)
                   ORDER BY {ITEM_ORDER_SQL}""".format(ITEM_ORDER_SQL=ITEM_ORDER_SQL),
                (contract_no, contract_no)).fetchall()
        w.writerow(header)
        w.writerow(["Generated", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")])
        w.writerow([])
        w.writerow(["Section", "Section title", "Code", "Item", "Provided", "Verified",
                    "Contents", "Pages", "Notes"])
        for s, st, code, item, prov, ver, text, pages, notes in rows:
            w.writerow([s, st or "", code, item or "", prov or "", ver or "",
                        text or "", " ".join(pages or []), notes or ""])
    else:
        with api_session(user["uid"]) as conn:
            row = conn.execute(
                "SELECT followups_json, version FROM contract_reports "
                "WHERE contract_no=%s ORDER BY version DESC LIMIT 1", (contract_no,)).fetchone()
        fus = (row[0] if row else None) or []
        w.writerow(header)
        w.writerow([])
        w.writerow(["No", "Matter", "Findings", "Actions", "Pages"])
        for i, fu in enumerate(fus, 1):
            w.writerow([i, fu.get("matter") or "",
                        "\n".join(str(x) for x in (fu.get("finding") or [])),
                        "\n".join(str(x) for x in (fu.get("action") or [])),
                        fu.get("pages") or ""])
    suffix = "checklist" if what == "items" else "followups"
    return Response(content=("\ufeff" + buf.getvalue()).encode("utf-8"),
                    media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f'attachment; filename="{contract_no}_{suffix}.csv"'})



@router.get("/api/contracts/{contract_no}/original")
def download_original(contract_no: str, user: dict = Depends(require_perm("contract.download"))):
    with api_session(user["uid"]) as conn:
        row = conn.execute("SELECT source_path, archived_at FROM contracts WHERE contract_no=%s", (contract_no,)).fetchone()
    if row is None:
        raise HTTPException(404, "Contract not found")
    _ensure_contract_readable(user, row[1] is not None)
    path = resolve_path(row[0])
    if path is None or not path.exists():
        raise HTTPException(404, "Original file missing on disk")
    return FileResponse(path, filename=f"{contract_no}_original.pdf", media_type="application/pdf")


@router.get("/api/contracts/{contract_no}/report")
def download_report(contract_no: str, user: dict = Depends(require_perm("contract.download"))):
    with api_session(user["uid"]) as conn:
        crow = conn.execute("SELECT archived_at FROM contracts WHERE contract_no=%s",
                            (contract_no,)).fetchone()
        if crow is None:
            raise HTTPException(404, "Contract not found")
        _ensure_contract_readable(user, crow[0] is not None)
        row = conn.execute(
            "SELECT report_path FROM contract_reports WHERE contract_no=%s ORDER BY version DESC LIMIT 1",
            (contract_no,)).fetchone()
    if row is None or not row[0]:
        raise HTTPException(404, "Report not generated yet")
    path = resolve_path(row[0])
    if path is None or not path.exists():
        raise HTTPException(404, "Report file missing on disk")
    return FileResponse(path, filename=f"{contract_no}_report.pdf", media_type="application/pdf")


@router.get("/api/contracts/{contract_no}/edited-report")
def download_edited_report(contract_no: str,
                           user: dict = Depends(require_perm("contract.download"))):
    """Export the current (manually edited) checklist with the original PDF layout.

    Edited cells keep the same font and size but are italic + deep blue.
    Reuses the skill renderer; the DB rows override the stored model JSON.
    """
    with api_session(user["uid"]) as conn:
        crow = conn.execute("SELECT archived_at FROM contracts WHERE contract_no=%s",
                            (contract_no,)).fetchone()
        if crow is None:
            raise HTTPException(404, "Contract not found")
        _ensure_contract_readable(user, crow[0] is not None)
    from ..report_pdf import ExportError, build as build_edited
    try:
        path = build_edited(contract_no)
    except ExportError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(404, msg)
        if "not been edited" in msg or "not generated" in msg:
            raise HTTPException(409, msg)
        raise HTTPException(500, f"Edited PDF export failed: {msg}")
    except Exception as e:
        raise HTTPException(500, f"Edited PDF export failed: {e}")

    audit(user["uid"], "export_edited_report", "contract", contract_no, {"path": str(path)})
    return FileResponse(path, filename=f"{contract_no}_report_edited.pdf",
                        media_type="application/pdf",
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})


@router.post("/api/contracts/{contract_no}/retry")
def retry_contract(contract_no: str, force: bool = False, confirm: bool = False,
                   user: dict = Depends(require_perm("contract.upload"))):
    """Re-queue analysis for a contract (retry a failure, re-run a success, or
    resume one that was waiting for LLM credentials).

    Ownership is enforced via RLS on the read; the write itself uses the server
    connection because client roles are intentionally forbidden from touching
    job rows.

    `force=1` is the escape hatch for an "analyzing" job whose worker died:
    without it the contract would sit at "正在分析" until the 120-minute lease
    expired, with no button that could fix it. Force is still refused while a
    live worker holds an unexpired lease, so a normal run cannot be knocked over.

    `confirm=1` is required when the latest report was manually edited; the UI
    shows a second warning and passes it only after explicit confirmation.
    """
    with api_session(user["uid"]) as conn:
        row = conn.execute("SELECT status, archived_at FROM contracts WHERE contract_no=%s",
                           (contract_no,)).fetchone()
        edited = conn.execute(
            """SELECT r.edited_at IS NOT NULL
                 FROM contract_reports r WHERE r.contract_no=%s ORDER BY r.version DESC LIMIT 1""",
            (contract_no,)).fetchone()
    if row is None:
        raise HTTPException(404, "Contract not found")
    if row[1] is not None:
        raise HTTPException(409, "Archived contracts cannot be re-analysed; restore them first")
    current = row[0]
    # A manually edited report must never be silently replaced by a new run.
    # The UI asks a second time; this is the server-side backstop for direct
    # API calls or an older client that has not been updated.
    if edited and edited[0] and not confirm:
        raise HTTPException(409, "REPORT_EDITED_CONFIRM_REQUIRED")
    if current == "analyzing":
        ws = worker_status()
        with server_conn() as conn:
            lease = conn.execute(
                """SELECT worker_id, EXTRACT(EPOCH FROM (lease_until - now()))::int
                     FROM analysis_jobs WHERE contract_no=%s AND status='analyzing'
                    ORDER BY id DESC LIMIT 1""", (contract_no,)).fetchone()
        # Who holds the lease matters, not whether *some* worker is alive: after a
        # worker crash the restarted process is alive but does not own this lease,
        # and the job would otherwise sit on "analyzing" for the full 120 minutes.
        lease_owner = lease[0] if lease else None
        lease_seconds_left = lease[1] if lease and lease[1] is not None else None
        owner_alive = any(w.get("worker_id") == lease_owner and w.get("alive")
                          for w in ws["workers"])
        if not force:
            hint = "worker is offline — retry with force=1" if not owner_alive else "try again later"
            raise HTTPException(409, f"Analysis is already running; {hint}")
        if owner_alive and (lease_seconds_left is None or lease_seconds_left > 0):
            raise HTTPException(409, "A live worker is still analysing this contract")
    if current == "queued":
        # Idempotent: a retry must not stack duplicate queued jobs.
        return {"ok": True, "contract_no": contract_no, "status": "queued", "already": True}

    # 'worker_lost' vs 'superseded' is not cosmetic: the audit trail has to say
    # why a job that was mid-flight got killed.
    supersede_code = "worker_lost" if current == "analyzing" else "superseded"
    with server_conn() as conn:
        # Any older job for this contract is superseded by this manual retry.
        conn.execute(
            """UPDATE analysis_jobs
                  SET status='failed', error_code=%s,
                      error_msg='superseded by manual retry',
                      finished_at=now(), lease_until=NULL
                WHERE contract_no=%s AND status IN ('queued','analyzing','awaiting_llm','failed')""",
            (supersede_code, contract_no))
        conn.execute(
            "INSERT INTO analysis_jobs (contract_no, attempt, status) VALUES (%s,1,'queued')",
            (contract_no,))
        conn.execute("UPDATE contracts SET status='queued' WHERE contract_no=%s", (contract_no,))
    audit(user["uid"], "retry", "contract", contract_no,
          {"from_status": current, "force": bool(force), "confirm_edited": bool(confirm),
           "code": supersede_code})
    return {"ok": True, "contract_no": contract_no, "status": "queued"}


@router.delete("/api/contracts/{contract_no}")
def delete_contract(contract_no: str, user: dict = Depends(require_perm("contract.upload"))):
    """Delete a contract, its reports/items/jobs and the files on disk.

    Only the owner or a superuser may delete."""
    with api_session(user["uid"]) as conn:
        row = conn.execute(
            "SELECT owner_id, source_path, archived_at FROM contracts WHERE contract_no=%s",
            (contract_no,)).fetchone()
    if row is None:
        raise HTTPException(404, "Contract not found")
    if row[2] is not None:
        raise HTTPException(409, "Archived contracts cannot be deleted; restore them first")
    owner_id, source_path = row[:2]
    if owner_id != user["uid"] and not user.get("is_superuser"):
        raise HTTPException(403, "Only the owner or an administrator can delete this contract")

    with server_conn() as conn:
        conn.execute(
            """DELETE FROM report_items WHERE report_id IN
                   (SELECT id FROM contract_reports WHERE contract_no=%s)""", (contract_no,))
        conn.execute("DELETE FROM contract_reports WHERE contract_no=%s", (contract_no,))
        conn.execute("DELETE FROM analysis_jobs WHERE contract_no=%s", (contract_no,))
        conn.execute("DELETE FROM contracts WHERE contract_no=%s", (contract_no,))

    # Disk cleanup is best-effort: a leftover folder must not fail the request.
    removed: list[str] = []
    try:
        if source_path:
            src = resolve_path(source_path)
            d = src.parent if src else None
            if d is not None and d.exists() and d.name == contract_no:
                shutil.rmtree(d, ignore_errors=True)
                removed.append(str(d))
        rep_dir = REPORTS_DIR / contract_no
        if rep_dir.exists():
            shutil.rmtree(rep_dir, ignore_errors=True)
            removed.append(str(rep_dir))
    except Exception as e:  # pragma: no cover
        audit(user["uid"], "delete_cleanup_warn", "contract", contract_no, {"error": str(e)[:200]})
    audit(user["uid"], "delete", "contract", contract_no,
          {"owner_id": owner_id, "files": removed})
    return {"ok": True, "contract_no": contract_no, "deleted": True, "files": removed}
