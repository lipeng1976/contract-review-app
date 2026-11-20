"""Single source of truth for on-disk file paths.

All files managed by the app live under ``CONTRACT_DATA_DIR`` (from .env).
The database stores ONLY paths relative to that root, e.g.::

    contracts/CT-20261118-QG6NAJ/original.pdf
    reports/CT-20261118-DBHWNM/v1/report.pdf

Why relative paths in the DB
----------------------------
A relative path makes the whole checkout movable: copy the code + data
directory to another machine, point CONTRACT_DATA_DIR at it, and every
download / render / re-analysis keeps working. With absolute paths the DB
would pin files to one machine's drive layout.

Backward compatibility
----------------------
Older rows still hold absolute paths (from before 2026-11-20). ``resolve_path``
transparently accepts both formats, so nothing breaks before the migration
script runs. New writes always use ``to_relative``.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath

from .db import CFG

ROOT = Path(__file__).resolve().parent.parent

# Root for all runtime files. Configurable via .env; defaults to backend/data
# for a fresh self-contained checkout.
DATA_DIR = Path((CFG.get("CONTRACT_DATA_DIR") or "").strip() or (ROOT / "data"))
CONTRACTS_DIR = DATA_DIR / "contracts"
REPORTS_DIR = DATA_DIR / "reports"
TMP_DIR = DATA_DIR / "tmp"
for _d in (CONTRACTS_DIR, REPORTS_DIR, TMP_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _is_absolute(text: str) -> bool:
    """True for Windows (drive/UNC) and POSIX absolute paths.

    pathlib.Path.is_absolute() is platform-dependent: a stored Windows path
    like ``D:\\...`` is NOT considered absolute when running on Linux (and vice
    versa). Check both flavours explicitly so legacy rows resolve correctly.
    """
    return PureWindowsPath(text).is_absolute() or PurePosixPath(text).is_absolute()


def resolve_path(stored: str | Path | None) -> Path | None:
    """Turn a DB-stored path into a real local path.

    - relative -> anchored at DATA_DIR (the new canonical format)
    - absolute -> returned as-is (legacy rows, until migrated)
    - empty     -> None
    """
    if stored is None:
        return None
    text = str(stored).strip()
    if not text:
        return None
    p = Path(text)
    if _is_absolute(text):
        return p
    return DATA_DIR / p


def to_relative(path: str | Path) -> str:
    """Turn a local path into the relative form stored in the DB.

    Paths under DATA_DIR become slash-separated relative paths (portable).
    Anything outside DATA_DIR is returned as-is with its original spelling so
    a legacy absolute path is never silently rewritten into a wrong location.
    """
    p = Path(path)
    text = str(p)
    if _is_absolute(text):
        try:
            return p.resolve().relative_to(DATA_DIR.resolve()).as_posix()
        except (ValueError, OSError):
            return text
    return PurePosixPath(text).as_posix()
