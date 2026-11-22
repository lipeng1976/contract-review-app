"""Shared credentials for the E2E scripts (never hard-code real passwords).

Values are read, in order, from:
  1. environment variables  TEST_ADMIN_PASSWORD / TEST_MEMBER_PASSWORD / ...
  2. the project-root .env  (git-ignored; fill it in locally to run these tests)
With no configuration, obvious placeholders are used so a public clone can
never authenticate as the owner.
"""
from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _ROOT / ".env"


def _v(key: str, default: str = "") -> str:
    if os.environ.get(key):
        return os.environ[key]
    if _ENV_FILE.is_file():
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, val = line.split("=", 1)
            if k.strip() == key:
                return val.strip().strip(chr(34)).strip(chr(39))
    return default


ADMIN = {"username": _v("TEST_ADMIN_USERNAME", "admin"),
         "password": _v("TEST_ADMIN_PASSWORD", "CHANGE_ME_ADMIN_PASSWORD")}
MEMBER = {"username": _v("TEST_MEMBER_USERNAME", "member.alpha"),
          "password": _v("TEST_MEMBER_PASSWORD", "CHANGE_ME_MEMBER_PASSWORD")}
EDITOR = MEMBER
OWNER = MEMBER
NEW_USER_PASSWORD = _v("TEST_NEW_USER_PASSWORD", "CHANGE_ME_NEW_USER_PASSWORD")
