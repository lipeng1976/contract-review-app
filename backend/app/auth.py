"""Authentication: bcrypt passwords, JWT in HttpOnly cookie, status gate."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import HTTPException, Request

from .db import CFG, server_conn

SECRET = CFG.get("JWT_SECRET", "")
if not SECRET:
    raise RuntimeError("JWT_SECRET missing in .env (copy .env.example first)")
COOKIE = "cr_session"
TOKEN_HOURS = 8


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def make_token(uid: int, role: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": str(uid), "role": role, "iat": now, "exp": now + timedelta(hours=TOKEN_HOURS)},
        SECRET, algorithm="HS256")


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired session")


def _rbac_for(uid: int) -> tuple[list[str], list[str], bool]:
    """Return (permissions, roles, is_superuser) from the RBAC tables.

    Permissions are *always* taken literally from role_permissions — a
    superuser flag no longer injects every capability. That is what lets the
    admin account be a pure system administrator (roles / users / LLM config)
    without silently holding contract upload or edit rights.
    """
    with server_conn() as conn:
        roles = [r[0] for r in conn.execute(
            """SELECT r.name FROM user_roles ur JOIN roles r ON r.id=ur.role_id
               WHERE ur.uid=%s ORDER BY r.id""", (uid,)).fetchall()]
        superuser = bool(conn.execute(
            """SELECT 1 FROM user_roles ur JOIN roles r ON r.id=ur.role_id
               WHERE ur.uid=%s AND r.is_superuser LIMIT 1""", (uid,)).fetchone())
        perms = [r[0] for r in conn.execute(
            """SELECT DISTINCT rp.perm_code FROM user_roles ur
               JOIN role_permissions rp ON rp.role_id=ur.role_id
               WHERE ur.uid=%s ORDER BY rp.perm_code""", (uid,)).fetchall()]
    return perms, roles, superuser


def current_user(request: Request) -> dict:
    """Resolve the user from cookie and re-read status/role/permissions from DB
    on every request, so approval, suspension and role changes apply instantly
    (no stale JWT)."""
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Not signed in")
    payload = decode_token(token)
    try:
        uid = int(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(401, "Invalid session payload")
    with server_conn() as conn:
        row = conn.execute(
            "SELECT uid, username, display_name, role, status FROM users WHERE uid=%s", (uid,)
        ).fetchone()
    if row is None:
        raise HTTPException(401, "Account no longer exists")
    perms, roles, superuser = _rbac_for(uid)
    # `phone` kept as an alias for older clients; both keys hold the account string.
    return dict(zip(
        ("uid", "username", "phone", "display_name", "role", "status",
         "permissions", "roles", "is_superuser"),
        (row[0], row[1], row[1], row[2], row[3], row[4], perms, roles, superuser)))


def require_active(request: Request) -> dict:
    """FastAPI dependency: active-status gate.
    NOTE: must take Request, not `user: dict` directly — a bare dict parameter
    makes FastAPI treat it as a required JSON body on GET endpoints.
    """
    user = current_user(request)
    if user["status"] != "active":
        raise HTTPException(403, f"Account status is {user['status']}; approval required")
    return user


def require_perm(code: str):
    """Dependency factory: gate an endpoint on one capability point.

    Permissions are read literally from role_permissions in current_user(),
    including for superusers. The built-in admin role carries only admin.*
    codes, so business endpoints stay closed to it; an administrator who also
    needs contract access must be assigned an additional business role.
    """
    def _dep(request: Request) -> dict:
        user = require_active(request)
        if code not in user["permissions"]:
            raise HTTPException(403, f"Permission required: {code}")
        return user
    return _dep


def require_admin(request: Request) -> dict:
    """Backward-compatible superuser gate (all admin endpoints)."""
    user = require_active(request)
    if not user["is_superuser"]:
        raise HTTPException(403, "Admin role required")
    return user
