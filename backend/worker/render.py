"""S5: render the model JSON to an English/Chinese PDF using the skill's script.

Reuses build_review_report.py verbatim (same model schema) so M2 output is
byte-compatible with the manual skill workflow. Runs it in a subprocess with
the default env Python (the one that has reportlab).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from app.db import CFG

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SKILL_BUILD_RAW = (CFG.get("SKILL_BUILD_SCRIPT") or "").strip()
# Path("") == Path(".") and exists(); that would render against the cwd.
SKILL_BUILD = Path(SKILL_BUILD_RAW) if SKILL_BUILD_RAW else None
if SKILL_BUILD is None or not SKILL_BUILD.is_file():
    SKILL_BUILD = ROOT / "skills" / "property-contract-review-report" / "scripts" / "build_review_report.py"
PYTHON = Path(sys.executable)


def render(model: dict, model_path: Path, out_path: Path, lang: str = "en") -> Path:
    if not SKILL_BUILD.exists():
        raise FileNotFoundError(f"skill renderer not found: {SKILL_BUILD}")
    model_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    cmd = [str(PYTHON), str(SKILL_BUILD), "--model", str(model_path),
           "--out", str(out_path), "--lang", lang]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"renderer failed rc={proc.returncode}: {proc.stdout[-500:]} {proc.stderr[-800:]}")
    return out_path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--lang", default="en", choices=["en", "zh"])
    a = ap.parse_args()
    m = json.loads(Path(a.model).read_text(encoding="utf-8"))
    p = render(m, Path(a.model + ".resolved.json"), Path(a.out), a.lang)
    print("rendered:", p, sha256(p))
