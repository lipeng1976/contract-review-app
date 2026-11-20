"""Bootstrap the first system administrator. Run on the server, not from the app.

    BOOTSTRAP_PASSWORD='<strong-password>' python -m db.bootstrap_admin <account> [display_name]

The password is read from the environment so it never lands in shell history or
in a committed file. Idempotent: an existing account is promoted to active/admin;
the login name is a general account (3-64 chars), not necessarily a phone number.

NOTE: `admin` is a pure system role (users / roles / audit / LLM settings).
Grant a business role (operator / viewer / archivist) separately if the same
person also needs to work with contracts.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.auth import hash_password            # noqa: E402
from app.db import server_conn                # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    account = sys.argv[1]
    display_name = sys.argv[2] if len(sys.argv) > 2 else "系统管理员"
    password = os.environ.get("BOOTSTRAP_PASSWORD", "")
    if not password:
        raise SystemExit("BOOTSTRAP_PASSWORD is required (do not pass it on the command line)")
    if len(password) < 12:
        raise SystemExit("Password must be at least 12 characters")

    with server_conn() as conn:
        admin_role = conn.execute("SELECT id FROM roles WHERE name='admin'").fetchone()
        if admin_role is None:
            raise SystemExit("Built-in 'admin' role is missing; run db/seed.sql first")
        admin_rid = admin_role[0]

        row = conn.execute("SELECT uid FROM users WHERE username=%s", (account,)).fetchone()
        if row:
            uid = row[0]
            conn.execute(
                "UPDATE users SET role='admin', status='active', password_hash=%s WHERE uid=%s",
                (hash_password(password), uid))
            action = "promoted"
        else:
            uid = conn.execute(
                """INSERT INTO users (username, password_hash, display_name, role, status)
                   VALUES (%s,%s,%s,'admin','active') RETURNING uid""",
                (account, hash_password(password), display_name)).fetchone()[0]
            action = "created"

        # RBAC reads permissions from user_roles, not from users.role.
        conn.execute(
            """INSERT INTO user_roles (uid, role_id) VALUES (%s,%s)
               ON CONFLICT DO NOTHING""", (uid, admin_rid))
    print(f"admin {action}: uid={uid} account={account}")


if __name__ == "__main__":
    main()
