"""Shared constants and helpers for the Contract Review API.

Kept dependency-free of FastAPI routers so both admin and contract modules can
import the same gating/audit logic. No secrets live here: configuration is read
from .env by app.db.
"""
from __future__ import annotations

import re
import secrets
import string
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, HTTPException, Request

from .auth import require_active
from .db import CFG, db_status, server_conn
from .paths import DATA_DIR, CONTRACTS_DIR, REPORTS_DIR, TMP_DIR, resolve_path, to_relative

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT.parent / "frontend" / "h5"
# Filesystem paths are centralised in app/paths.py. CONTRACT_DATA_DIR (see .env)
# is the root; the DB stores paths relative to it. Import/re-export here so
# existing routers keep working with `from ..core import DATA_DIR, ...`.

APP_NAME = "contract-review-app"
APP_VERSION = "0.8.0-progress"
PUBLIC_BASE_URL = (CFG.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
MAX_UPLOAD = 100 * 1024 * 1024
WORKER_ALIVE_SECONDS = int(CFG.get("WORKER_ALIVE_SECONDS", "45"))
STALL_SECONDS = int(CFG.get("STALL_SECONDS", "600"))

# ---------- helpers ----------

def audit(actor_uid: int | None, action: str, target_type: str, target_id: str, detail: dict | None = None) -> None:
    with server_conn() as conn:
        conn.execute(
            "INSERT INTO audit_logs (actor_uid, action, target_type, target_id, detail) VALUES (%s,%s,%s,%s,%s)",
            (actor_uid, action, target_type, str(target_id), __import__("json").dumps(detail or {}, ensure_ascii=False)))


def new_contract_no() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return f"CT-{datetime.now().strftime('%Y%m%d')}-" + "".join(secrets.choice(alphabet) for _ in range(6))


# Account = general login name. Accept `username` (preferred) or legacy `phone`
# field from the client; both map to users.username. Allows letters/digits/. /_
# /- so mainland numbers, overseas emails-style names and handles all work.
ACCOUNT_RE = re.compile(r"^[A-Za-z0-9._-]{3,64}$")


def pick_account(payload: dict) -> str:
    raw = payload.get("username") or payload.get("phone") or ""
    return str(raw).strip()


def valid_account(value: str) -> bool:
    return bool(ACCOUNT_RE.fullmatch(value))


# ---------- RBAC helpers ----------

def require_contract_read(request: Request) -> dict:
    """Read gate for contract data: either normal or archive-view permission.

    The endpoint then decides which one applies after it knows whether the
    contract is archived — a user with only archive rights must not read live
    contracts through the archived route, and vice versa. Must stay above the
    first @router.get that uses it (Depends is evaluated at import time).
    """
    user = require_active(request)
    if not ({"contract.view", "contract.archive_view"} & set(user["permissions"])):
        raise HTTPException(403, "Permission required: contract.view or contract.archive_view")
    return user


def _ensure_contract_readable(user: dict, archived: bool) -> None:
    need = "contract.archive_view" if archived else "contract.view"
    if need not in user["permissions"]:
        raise HTTPException(403, f"Permission required: {need}")


def role_by_name(conn, name: str):
    return conn.execute("SELECT id,name,label_zh,label_en,is_system,is_superuser FROM roles WHERE name=%s", (name,)).fetchone()


def assign_roles(conn, uid: int, names: list[str], actor_uid: int | None = None) -> list[str]:
    """Replace a user's roles with `names` (all must exist). Returns role names."""
    names = list(dict.fromkeys(names))  # dedupe, keep order
    if not names:
        raise HTTPException(400, "At least one role is required")
    resolved = []
    for n in names:
        r = role_by_name(conn, n)
        if r is None:
            raise HTTPException(400, f"Unknown role: {n}")
        resolved.append(r)
    conn.execute("DELETE FROM user_roles WHERE uid=%s", (uid,))
    for r in resolved:
        conn.execute("INSERT INTO user_roles (uid,role_id) VALUES (%s,%s)", (uid, r[0]))
    # users.role = primary label (first role), kept for display / legacy clients.
    conn.execute("UPDATE users SET role=%s WHERE uid=%s", (resolved[0][1], uid))
    if actor_uid is not None:
        audit(actor_uid, "assign_roles", "user", uid, {"roles": names})
    return names


def user_roles(conn, uid: int) -> list[str]:
    return [r[0] for r in conn.execute(
        """SELECT r.name FROM user_roles ur JOIN roles r ON r.id=ur.role_id
           WHERE ur.uid=%s ORDER BY r.id""", (uid,)).fetchall()]


def rowdict(cur, row) -> dict:
    return {d.name: v for d, v in zip(cur.description, row)}


# Checklist codes are a section letter plus a number (A1..A12, G7). Ordering by
# the raw text puts A10 between A1 and A2, which reads as a bug in the app and
# in the CSV export. Sort by the numeric suffix first and keep the raw code as a
# tie-breaker, so A1 -> A1-X -> A2 -> ... -> A10. Codes added by hand can carry
# trailing letter/dash parts ("A1-X"), hence regexp_replace instead of a cast on
# the whole string; a suffix-less code lands last instead of erroring the query.
ITEM_ORDER_SQL = ("ri.section, "
                  "coalesce(nullif(regexp_replace(ri.code, '[^0-9]', '', 'g'), '')::numeric, 9999), "
                  "ri.code")


def worker_status() -> dict:
    """Is a worker actually running right now?

    This is the answer to the only question a "queued" contract raises:
    is somebody about to analyse it, or has nobody even started? Liveness comes
    from worker_heartbeats, not from the LLM being configured.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        with server_conn() as conn:
            cur = conn.execute(
                """SELECT worker_id, host, pid, status, job_id, contract_no,
                          stage, stage_msg, started_at, last_seen,
                          EXTRACT(EPOCH FROM (now()-last_seen))::int AS age_seconds
                     FROM worker_heartbeats ORDER BY last_seen DESC LIMIT 10""")
            workers = [rowdict(cur, r) for r in cur.fetchall()]
    except Exception:
        return {"alive": False, "count": 0, "workers": [], "checked_at": now_iso,
                "alive_seconds": WORKER_ALIVE_SECONDS, "error": "db_unavailable"}
    # ⚠️ 别写成 `age or 10**6`：心跳刚写入时 age_seconds 常为 0（同一秒内），
    # 0 是 falsy，会被当成「没有心跳」误判成 worker 死亡。看门狗连续 3 次
    # 撞上这个 0 就会无辜重启 worker —— 表现为桌面周期性弹出 python 窗口。
    # 只有真正的 NULL 才该当作不可用；负数（时钟回拨）按刚心跳处理。
    alive = []
    for w in workers:
        age = w.get("age_seconds")
        w["alive"] = (w.get("status") != "stopped"
                      and age is not None and age <= WORKER_ALIVE_SECONDS)
        if w["alive"]:
            alive.append(w)
        if w.get("last_seen") is not None:
            w["last_seen"] = w["last_seen"].isoformat()
        if w.get("started_at") is not None:
            w["started_at"] = w["started_at"].isoformat()
    return {"alive": bool(alive), "count": len(alive),
            "busy": any(w.get("status") == "busy" for w in alive),
            "last_seen": workers[0]["last_seen"] if workers else None,
            "workers": workers, "checked_at": now_iso,
            "alive_seconds": WORKER_ALIVE_SECONDS}


# ---------- health / config ----------

