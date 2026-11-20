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

@router.get("/health")
@router.get("/api/health")
def health():
    db = db_status()
    try:
        from worker.llm import config_summary as _llm_cfg
        llm_ready = _llm_cfg()["configured"]
    except Exception:
        llm_ready = False
    ws = worker_status()
    return {
        "status": "ok" if db == "up" else "degraded",
        "app": APP_NAME, "version": APP_VERSION,
        "time": datetime.now(timezone.utc).isoformat(),
        "components": {
            "api": "up", "static_page": "up", "database": db,
            # Two different things used to be reported under one name: whether
            # the LLM is configured, and whether the Worker process is running.
            # A queued contract only advances if the process is alive.
            "worker": "alive" if ws["alive"] else "not_running",
            "worker_busy": ws.get("busy", False),
            "worker_last_seen": ws.get("last_seen"),
            "llm_configured": llm_ready,
        },
    }


@router.get("/api/system/worker-status")
def system_worker_status(user: dict = Depends(require_active)):
    """Liveness of the analysis Worker (does NOT require any admin permission:
    every uploader needs to know whether their file will ever be analysed)."""
    ws = worker_status()
    ws["stall_seconds"] = STALL_SECONDS
    if ws["workers"]:
        ws["workers"] = [{k: w[k] for k in
                          ("worker_id", "host", "pid", "status", "job_id", "contract_no",
                           "stage", "stage_msg", "started_at", "last_seen", "age_seconds", "alive")}
                         for w in ws["workers"]]
    return ws


@router.get("/api/config")
def client_config():
    return {"publicBaseUrl": PUBLIC_BASE_URL or None, "apiBase": ""}


# ---------- auth ----------
