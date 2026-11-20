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

@router.get("/api/admin/pending-users")
def pending_users(admin: dict = Depends(require_perm("admin.users"))):
    with api_session(admin["uid"]) as conn:
        cur = conn.execute(
            "SELECT uid, username, display_name, created_at FROM users WHERE status='pending' ORDER BY uid")
        return {"users": [rowdict(cur, r) for r in cur.fetchall()]}


@router.post("/api/admin/users/{uid}/approve")
def approve_user(uid: int, payload: dict | None = None, admin: dict = Depends(require_perm("admin.users"))):
    payload = payload or {}
    # Admin decides the final role at approval time (default: viewer / least privilege).
    roles = payload.get("roles") or [payload.get("role") or "viewer"]
    roles = [str(r).strip() for r in roles]
    with server_conn() as conn:
        # Only a superuser may grant a superuser role (no self-escalation).
        if not admin["is_superuser"]:
            granted = {r[0] for r in conn.execute(
                "SELECT name FROM roles WHERE is_superuser").fetchall()}
            if granted.intersection(roles):
                raise HTTPException(403, "Only a superuser can grant the admin role")
        cur = conn.execute(
            "UPDATE users SET status='active', approved_by=%s, approved_at=now() "
            "WHERE uid=%s AND status='pending' RETURNING uid", (admin["uid"], uid))
        if cur.fetchone() is None:
            raise HTTPException(404, "Pending user not found")
        final_roles = assign_roles(conn, uid, roles, actor_uid=admin["uid"])
    audit(admin["uid"], "approve_user", "user", uid, {"roles": final_roles})
    return {"ok": True, "uid": uid, "status": "active", "roles": final_roles}


@router.post("/api/admin/users/{uid}/reject")
def reject_user(uid: int, admin: dict = Depends(require_perm("admin.users"))):
    with api_session(admin["uid"]) as conn:
        cur = conn.execute("UPDATE users SET status='rejected' WHERE uid=%s AND status='pending' RETURNING uid", (uid,))
        if cur.fetchone() is None:
            raise HTTPException(404, "Pending user not found")
    audit(admin["uid"], "reject_user", "user", uid)
    return {"ok": True, "uid": uid, "status": "rejected"}


@router.post("/api/admin/users/{uid}/suspend")
def suspend_user(uid: int, admin: dict = Depends(require_perm("admin.users"))):
    with server_conn() as conn:
        # Only a superuser may suspend another superuser.
        if "admin" in user_roles(conn, uid) and not admin["is_superuser"]:
            raise HTTPException(403, "Only a superuser can suspend an admin")
        cur = conn.execute("UPDATE users SET status='suspended' WHERE uid=%s RETURNING uid", (uid,))
        if cur.fetchone() is None:
            raise HTTPException(404, "User not found")
    audit(admin["uid"], "suspend_user", "user", uid)
    return {"ok": True, "uid": uid, "status": "suspended"}


# ---------- admin: LLM config (admin-editable, hot-reloaded by worker) ----------

def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 4:
        return "****"
    return "****" + key[-4:]


@router.get("/api/admin/llm-config")
def get_llm_config(admin: dict = Depends(require_perm("admin.llm"))):
    with api_session(admin["uid"]) as conn:
        row = conn.execute(
            "SELECT c.base_url,c.api_key,c.model,c.model_vision,c.timeout,c.max_tokens,"
            "       c.enabled,c.updated_at,coalesce(u.display_name,u.username) "
            "FROM llm_config c LEFT JOIN users u ON u.uid=c.updated_by WHERE c.id=1").fetchone()
    if row is None:
        raise HTTPException(404, "LLM config not initialized; run db/migrate_llm_config.py")
    # The saved row IS the system parameter: the Worker re-reads it for every job,
    # so a save takes effect immediately and stays until the next save.
    effective = bool(row[6] and row[0] and row[1] and row[2])
    return {"base_url": row[0], "api_key_masked": _mask_key(row[1]), "has_key": bool(row[1]),
            "model": row[2], "model_vision": row[3], "timeout": row[4],
            "max_tokens": row[5], "enabled": row[6],
            "updated_at": row[7].isoformat() if row[7] else None,
            "updated_by": row[8], "effective": effective,
            "source": "database", "refresh": "immediate"}


@router.put("/api/admin/llm-config")
def put_llm_config(payload: dict, admin: dict = Depends(require_perm("admin.llm"))):
    # A masked key ("****abcd") from the UI means "keep existing key".
    raw_key = str(payload.get("api_key", ""))
    base_url = str(payload.get("base_url", "")).strip().rstrip("/")
    model = str(payload.get("model", "")).strip()
    vision = str(payload.get("model_vision", "")).strip()
    timeout = max(5, min(600, int(payload.get("timeout", 120))))
    max_tokens = max(256, min(65536, int(payload.get("max_tokens", 8192))))
    enabled = bool(payload.get("enabled", True))
    with api_session(admin["uid"]) as conn:
        current = conn.execute("SELECT api_key FROM llm_config WHERE id=1").fetchone()
        if current is None:
            raise HTTPException(404, "LLM config not initialized")
        new_key = current[0] if raw_key.startswith("****") or raw_key == "" else raw_key
        if raw_key == "__CLEAR__":
            new_key = ""
        conn.execute(
            """UPDATE llm_config SET base_url=%s,api_key=%s,model=%s,model_vision=%s,
               timeout=%s,max_tokens=%s,enabled=%s,updated_at=now(),updated_by=%s WHERE id=1""",
            (base_url, new_key, model, vision, timeout, max_tokens, enabled, admin["uid"]))
    audit(admin["uid"], "update_llm_config", "llm_config", 1,
          {"base_url": base_url, "model": model, "enabled": enabled, "key_changed": new_key != current[0]})
    return get_llm_config(admin)


@router.post("/api/admin/llm-config/test")
def test_llm_config(admin: dict = Depends(require_perm("admin.llm"))):
    from worker.llm import test_connection
    return test_connection()


# ---------- admin: RBAC roles & user roles ----------

def _role_dict(cur, row) -> dict:
    d = rowdict(cur, row)
    return d


@router.get("/api/admin/permissions")
def list_permissions(admin: dict = Depends(require_perm("admin.roles"))):
    with server_conn() as conn:
        cur = conn.execute("SELECT code,category,label_zh,label_en FROM permissions ORDER BY category,code")
        return {"permissions": [rowdict(cur, r) for r in cur.fetchall()]}


@router.get("/api/admin/roles")
def list_roles(admin: dict = Depends(require_perm("admin.roles"))):
    with server_conn() as conn:
        cur = conn.execute(
            """SELECT id,name,label_zh,label_en,description,is_system,is_superuser,created_at
               FROM roles ORDER BY is_superuser DESC, id""")
        roles = []
        for r in cur.fetchall():
            d = rowdict(cur, r)
            d["permissions"] = [x[0] for x in conn.execute(
                "SELECT perm_code FROM role_permissions WHERE role_id=%s ORDER BY perm_code", (d["id"],)).fetchall()]
            d["member_count"] = conn.execute(
                "SELECT count(*) FROM user_roles WHERE role_id=%s", (d["id"],)).fetchone()[0]
            d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
            roles.append(d)
        return {"roles": roles}


@router.post("/api/admin/roles")
def create_role(payload: dict, admin: dict = Depends(require_perm("admin.roles"))):
    name = str(payload.get("name", "")).strip().lower()
    label_zh = str(payload.get("label_zh", "")).strip()[:80]
    label_en = str(payload.get("label_en", "")).strip()[:80]
    description = str(payload.get("description", "")).strip()[:300]
    perms = [str(p).strip() for p in (payload.get("permissions") or [])]
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,47}", name):
        raise HTTPException(400, "Role name must be 2-48 lowercase letters/digits/underscore, starting with a letter")
    if not label_zh or not label_en:
        raise HTTPException(400, "Both label_zh and label_en are required")
    with server_conn() as conn:
        if conn.execute("SELECT 1 FROM roles WHERE name=%s", (name,)).fetchone():
            raise HTTPException(409, "Role name already exists")
        # Validate permission codes before inserting anything.
        known = {r[0] for r in conn.execute("SELECT code FROM permissions").fetchall()}
        unknown = [p for p in perms if p not in known]
        if unknown:
            raise HTTPException(400, f"Unknown permission(s): {', '.join(unknown)}")
        rid = conn.execute(
            """INSERT INTO roles (name,label_zh,label_en,description,is_system,is_superuser,created_by)
               VALUES (%s,%s,%s,%s,false,false,%s) RETURNING id""",
            (name, label_zh, label_en, description, admin["uid"])).fetchone()[0]
        for p in perms:
            conn.execute("INSERT INTO role_permissions (role_id,perm_code) VALUES (%s,%s)", (rid, p))
    audit(admin["uid"], "create_role", "role", name, {"permissions": perms})
    return {"ok": True, "id": rid, "name": name}


@router.put("/api/admin/roles/{rid}")
def update_role(rid: int, payload: dict, admin: dict = Depends(require_perm("admin.roles"))):
    with server_conn() as conn:
        row = conn.execute("SELECT id,name,is_system,is_superuser FROM roles WHERE id=%s", (rid,)).fetchone()
        if row is None:
            raise HTTPException(404, "Role not found")
        name, system, superuser = row[1], row[2], row[3]
        # System roles: labels/description editable, but name & superuser flag locked;
        # permission set locked for the superuser role (it always holds everything).
        new_name = str(payload.get("name", name)).strip().lower()
        if system and new_name != name:
            raise HTTPException(400, "System role name cannot be changed")
        if not system and not re.fullmatch(r"[a-z][a-z0-9_]{1,47}", new_name):
            raise HTTPException(400, "Invalid role name")
        if not system and conn.execute("SELECT 1 FROM roles WHERE name=%s AND id<>%s", (new_name, rid)).fetchone():
            raise HTTPException(409, "Role name already exists")
        label_zh = str(payload.get("label_zh", "")).strip()[:80] or None
        label_en = str(payload.get("label_en", "")).strip()[:80] or None
        description = str(payload.get("description", "")).strip()[:300] or None
        conn.execute(
            """UPDATE roles SET name=COALESCE(%s,name), label_zh=COALESCE(%s,label_zh),
               label_en=COALESCE(%s,label_en), description=COALESCE(%s,description) WHERE id=%s""",
            (new_name, label_zh, label_en, description, rid))
        if "permissions" in payload and not superuser:
            perms = [str(p).strip() for p in (payload.get("permissions") or [])]
            known = {r[0] for r in conn.execute("SELECT code FROM permissions").fetchall()}
            unknown = [p for p in perms if p not in known]
            if unknown:
                raise HTTPException(400, f"Unknown permission(s): {', '.join(unknown)}")
            conn.execute("DELETE FROM role_permissions WHERE role_id=%s", (rid,))
            for p in perms:
                conn.execute("INSERT INTO role_permissions (role_id,perm_code) VALUES (%s,%s)", (rid, p))
    audit(admin["uid"], "update_role", "role", rid)
    return {"ok": True}


@router.delete("/api/admin/roles/{rid}")
def delete_role(rid: int, admin: dict = Depends(require_perm("admin.roles"))):
    with server_conn() as conn:
        row = conn.execute("SELECT name,is_system FROM roles WHERE id=%s", (rid,)).fetchone()
        if row is None:
            raise HTTPException(404, "Role not found")
        if row[1]:
            raise HTTPException(400, "System roles cannot be deleted")
        conn.execute("DELETE FROM roles WHERE id=%s", (rid,))  # cascades to role_permissions & user_roles
    audit(admin["uid"], "delete_role", "role", rid, {"name": row[0]})
    return {"ok": True}


@router.get("/api/admin/users")
def list_all_users(admin: dict = Depends(require_perm("admin.users"))):
    with server_conn() as conn:
        cur = conn.execute(
            """SELECT uid,username,display_name,role,status,created_at
               FROM users ORDER BY uid""")
        users = []
        for r in cur.fetchall():
            d = rowdict(cur, r)
            d["roles"] = user_roles(conn, d["uid"])
            d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
            users.append(d)
        return {"users": users}


@router.put("/api/admin/users/{uid}/roles")
def set_user_roles(uid: int, payload: dict, admin: dict = Depends(require_perm("admin.users"))):
    names = [str(r).strip() for r in (payload.get("roles") or [])]
    with server_conn() as conn:
        if conn.execute("SELECT 1 FROM users WHERE uid=%s", (uid,)).fetchone() is None:
            raise HTTPException(404, "User not found")
        # Only a superuser may grant a superuser role (no privilege escalation).
        if not admin["is_superuser"]:
            super_names = {r[0] for r in conn.execute("SELECT name FROM roles WHERE is_superuser").fetchall()}
            if super_names.intersection(names):
                raise HTTPException(403, "Only a superuser can grant the admin role")
        # Refuse to strip superuser from the last remaining admin (lockout guard).
        current = user_roles(conn, uid)
        final = assign_roles(conn, uid, names, actor_uid=admin["uid"])
        remaining_admins = conn.execute(
            """SELECT count(*) FROM user_roles ur JOIN roles r ON r.id=ur.role_id
               WHERE r.is_superuser AND ur.uid<>%s""", (uid,)).fetchone()[0]
        if "admin" in current and "admin" not in final and remaining_admins == 0:
            raise HTTPException(400, "Cannot remove the last superuser")
    return {"ok": True, "uid": uid, "roles": final}


# ---------- contracts ----------


@router.get("/api/admin/audit")
def admin_audit(limit: int = Query(200, le=1000), action: str | None = Query(None),
                q: str | None = Query(None),
                user: dict = Depends(require_perm("admin.audit"))):
    """Global operation log (who did what, when) — admins only."""
    sql = """SELECT a.id, a.action, a.target_type, a.target_id, a.detail, a.created_at,
                    coalesce(u.display_name, u.username, 'system') AS actor, u.username
             FROM audit_logs a LEFT JOIN users u ON u.uid=a.actor_uid WHERE 1=1"""
    args: list = []
    if action:
        sql += " AND a.action=%s"
        args.append(action)
    if q and q.strip():
        like = "%" + q.strip()[:60] + "%"
        sql += (" AND (coalesce(a.target_id,'') ILIKE %s OR a.action ILIKE %s "
                "OR coalesce(u.username,'') ILIKE %s OR coalesce(u.display_name,'') ILIKE %s)")
        args += [like] * 4
    sql += " ORDER BY a.id DESC LIMIT %s"
    args.append(limit)
    with server_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
        actions = [r[0] for r in conn.execute(
            "SELECT DISTINCT action FROM audit_logs ORDER BY action").fetchall()]
    return {"total": len(rows), "actions": actions,
            "logs": [{"id": r[0], "action": r[1], "target_type": r[2], "target": r[3],
                      "detail": r[4], "created_at": r[5].isoformat() if r[5] else None,
                      "actor": r[6], "username": r[7]} for r in rows]}


