"""Database access helpers.

Two connection modes:
  server_conn()  - postgres superuser, bypasses RLS. Used ONLY for auth
                   (no app.uid yet) and server-side writes (audit, bootstrap).
  api_session()  - app_api role with SET LOCAL app.uid, so RLS enforces
                   per-user isolation on every business query.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent

# Every supported setting must be readable from the process environment even
# when no .env file exists (containers / CI / smoke tests). Keep in sync with
# .env.example at the project root.
ENV_KEYS = (
    "APP_HOST", "APP_PORT", "PUBLIC_BASE_URL",
    "PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE",
    "PGAPIUSER", "PGAPIPASSWORD", "JWT_SECRET", "BOOTSTRAP_PASSWORD",
    "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_MODEL_VISION",
    "LLM_TIMEOUT", "LLM_MAX_TOKENS",
    "WORKER_POLL_SECONDS", "WORKER_LEASE_MINUTES",
    "WORKER_ALIVE_SECONDS", "STALL_SECONDS", "SKILL_BUILD_SCRIPT",
    "CONTRACT_DATA_DIR", "CONTRACT_ROOT", "CONTRACT_PGDATA", "CONTRACT_PGBIN",
    "CONTRACT_PY", "CONTRACT_PYW", "CONTRACT_NODE",
)


def load_env(path: Path) -> dict:
    cfg = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    # Environment wins over the file, including keys absent from the file.
    for k in ENV_KEYS:
        if k in os.environ:
            cfg[k] = os.environ[k]
    return cfg


# The handoff layout keeps the single .env at the project root (one level above
# /backend). Fall back to the legacy location for old checkouts.
ENV_FILE = ROOT.parent / ".env"
if not ENV_FILE.exists():
    ENV_FILE = ROOT / "config" / ".env"
CFG = load_env(ENV_FILE)

_SERVER = dict(
    host=CFG.get("PGHOST", "127.0.0.1"),
    port=int(CFG.get("PGPORT", "5434")),
    user=CFG.get("PGUSER", "postgres"),
    password=CFG.get("PGPASSWORD", ""),
    dbname=CFG.get("PGDATABASE", "contract_review"),
    connect_timeout=5,
)
_API = dict(_SERVER,
            user=CFG.get("PGAPIUSER", "app_api"),
            password=CFG.get("PGAPIPASSWORD", ""))


@contextmanager
def server_conn():
    """Superuser connection. NEVER use for user-facing reads/writes."""
    with psycopg.connect(**_SERVER) as conn:
        yield conn


@contextmanager
def api_session(uid: int | None):
    """app_api connection with transaction-local app.uid => RLS active.
    uid=None means no identity: all RLS-protected reads return 0 rows.
    """
    with psycopg.connect(**_API) as conn:
        conn.execute("SELECT set_config('app.uid', %s, true)", ("" if uid is None else str(uid),))
        yield conn


def db_status() -> str:
    try:
        with psycopg.connect(**_SERVER) as conn:
            conn.execute("select 1").fetchone()
        return "up"
    except Exception:
        return "down"
