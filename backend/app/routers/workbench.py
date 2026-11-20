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
                    APP_NAME, APP_VERSION, PUBLIC_BASE_URL, MAX_UPLOAD,
                    WORKER_ALIVE_SECONDS, STALL_SECONDS, audit, new_contract_no,
                    ACCOUNT_RE, pick_account, valid_account, require_contract_read,
                    _ensure_contract_readable, role_by_name, assign_roles, user_roles,
                    rowdict, ITEM_ORDER_SQL, worker_status)

router = APIRouter()

@router.get("/api/followups")
def all_followups(q: str | None = Query(None), contract_no: str | None = Query(None),
                  archived: bool = False, limit: int = Query(500, le=2000),
                  user: dict = Depends(require_contract_read)):
    """Worklist across visible contracts. Archived rows are excluded by default.

    `archived=1` intentionally flips to archived-only and requires the separate
    archive-view capability — a follow-up is a contract-derived feature, so it
    obeys the same archive rule as the contract itself.
    """
    if archived:
        if "contract.archive_view" not in user["permissions"]:
            raise HTTPException(403, "Permission required: contract.archive_view")
    else:
        _ensure_contract_readable(user, False)
    sql = """SELECT c.contract_no, coalesce(c.property_address, c.address, '') AS addr,
                    c.status, c.report_version, c.archived_at IS NOT NULL AS archived,
                    r.followups_json
             FROM contracts c
             JOIN contract_reports r ON r.contract_no=c.contract_no
                                    AND r.version=c.report_version
             WHERE r.followups_json IS NOT NULL
               AND c.archived_at IS """ + ("NOT NULL" if archived else "NULL")
    args: list = []
    if contract_no:
        sql += " AND c.contract_no=%s"
        args.append(contract_no)
    sql += " ORDER BY c.created_at DESC"
    needle = (q or "").strip().lower()
    items: list = []
    with api_session(user["uid"]) as conn:
        for cn, addr, status, ver, arch, fj in conn.execute(sql, args).fetchall():
            for fu in (fj or []):
                matter = str(fu.get("matter") or "").strip()
                finding = [str(x) for x in (fu.get("finding") or [])]
                action = [str(x) for x in (fu.get("action") or [])]
                if needle:
                    blob = " ".join([matter, *finding, *action, cn, str(addr or "")]).lower()
                    if needle not in blob:
                        continue
                items.append({"contract_no": cn, "address": addr, "status": status,
                              "version": ver, "archived": bool(arch), "matter": matter, "finding": finding,
                              "action": action, "pages": str(fu.get("pages") or "")})
    items = items[:limit]
    return {"total": len(items), "contracts": len({x["contract_no"] for x in items}),
            "items": items}


