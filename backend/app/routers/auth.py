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

@router.post("/api/auth/register")
def register(payload: dict):
    account = pick_account(payload)
    password = str(payload.get("password", ""))
    display_name = str(payload.get("display_name", "")).strip()[:80] or None
    # A registrant may request operator/viewer, never admin (superuser is granted
    # explicitly by an existing admin). Default to least privilege: viewer.
    requested_role = str(payload.get("role") or "viewer").strip()
    if requested_role not in ("viewer", "operator"):
        raise HTTPException(400, "Requested role must be viewer or operator")
    if not valid_account(account):
        raise HTTPException(400, "Account must be 3-64 chars: letters, digits, dot, underscore or hyphen")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    # Registration has no app.uid yet: use server connection deliberately.
    with server_conn() as conn:
        try:
            uid = conn.execute(
                """INSERT INTO users (username, password_hash, display_name, role, status)
                   VALUES (%s,%s,%s,%s,'pending') RETURNING uid""",
                (account, hash_password(password), display_name, requested_role)).fetchone()[0]
        except UniqueViolation:
            raise HTTPException(409, "Account already registered")
        assign_roles(conn, uid, [requested_role])
    audit(uid, "register", "user", uid, {"username": account, "requested_role": requested_role})
    return {"ok": True, "uid": uid, "username": account, "status": "pending", "role": requested_role,
            "message": "Registered; awaiting admin approval"}


@router.post("/api/auth/login")
def login(payload: dict):
    account = pick_account(payload)
    password = str(payload.get("password", ""))
    with server_conn() as conn:
        row = conn.execute(
            "SELECT uid, username, password_hash, display_name, role, status FROM users WHERE username=%s",
            (account,)).fetchone()
    if row is None or not verify_password(password, row[2]):
        raise HTTPException(401, "Invalid account or password")
    perms, roles, superuser = _rbac_for(row[0])
    user = dict(zip(("uid", "username", "phone", "display_name", "role", "status",
                     "permissions", "roles", "is_superuser"),
                    (row[0], row[1], row[1], row[3], row[4], row[5], perms, roles, superuser)))
    token = make_token(user["uid"], user["role"])
    resp = JSONResponse({"ok": True, "user": user})
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=TOKEN_HOURS * 3600, path="/")
    audit(user["uid"], "login", "user", user["uid"])
    return resp


@router.post("/api/auth/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


@router.get("/api/auth/me")
def me(user: dict = Depends(current_user)):
    return {"user": user}


@router.post("/api/auth/change-password")
def change_password(payload: dict, user: dict = Depends(require_active)):
    old = str(payload.get("old_password", ""))
    new = str(payload.get("new_password", ""))
    if len(new) < 8:
        raise HTTPException(400, "New password must be at least 8 characters")
    with server_conn() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE uid=%s", (user["uid"],)).fetchone()
        if row is None or not verify_password(old, row[0]):
            raise HTTPException(400, "Old password is incorrect")
        conn.execute("UPDATE users SET password_hash=%s WHERE uid=%s", (hash_password(new), user["uid"]))
    audit(user["uid"], "change_password", "user", user["uid"])
    return {"ok": True}


# ---------- admin ----------

