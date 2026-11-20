"""M2 end-to-end: worker replay -> render -> A-G ingest -> API query -> PDF.

Runs against the live backend. Uses the Hinkler regression model replayed onto
a dedicated contract owned by member.alpha so it is repeatable.

    python tests/e2e_m2.py

Prereq: backend running; a queued job for REPLAY_CONTRACT (this script resets
it), and _work/hinkler/model_v3.json present.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
BASE = "http://127.0.0.1:8790"
MEMBER = {"username": "member.alpha", "password": "TestMember2026"}
CONTRACT = "CT-20261118-Q8WQC4"
MODEL = ROOT.parent / "_work/hinkler/model_v3.json"

ok = 0
def check(label, cond, detail=""):
    global ok
    ok += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f" :: {detail}" if detail else ""))
    if not cond:
        raise SystemExit(1)


def main():
    # Reset the contract's job to queued, then run the worker in replay mode.
    sys.path.insert(0, str(ROOT))
    from app.db import server_conn
    with server_conn() as c:
        c.execute("UPDATE analysis_jobs SET status='queued',error_code=NULL,error_msg=NULL,lease_until=NULL WHERE contract_no=%s", (CONTRACT,))
        c.execute("UPDATE contracts SET status='queued',report_version=0 WHERE contract_no=%s", (CONTRACT,))
    proc = subprocess.run(
        [PY, "-m", "worker.runner", "--once",
         "--replay-model", str(MODEL), "--contract-no", CONTRACT],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180)
    out = proc.stdout + proc.stderr
    check("worker replay ran", proc.returncode == 0, out[-300:])
    import json as _json
    json_lines = [l for l in proc.stdout.splitlines() if l.strip().startswith('{')]
    check("worker emitted result JSON", bool(json_lines), out[-200:])
    result = _json.loads(json_lines[-1])
    check("worker targeted correct contract",
          "report_path" not in result or CONTRACT in result.get("report_path", ""),
          str(result.get("report_path")))
    check("worker status completed", result.get("status") == "completed", str(result)[:200])
    check("82 items ingested", result.get("items") == 82, str(result.get("stats")))
    check("gate review flagged", result.get("gate_review") is True, str(result.get("gate_reasons")))
    check("79-page PDF parsed", result.get("pages", {}).get("pages") == 79, str(result.get("pages")))

    # Query as the contract owner via API.
    c = httpx.Client(base_url=BASE, timeout=30)
    c.post("/api/auth/login", json=MEMBER)
    d = c.get(f"/api/contracts/{CONTRACT}").json()
    check("contract shows completed", d["contract"]["status"] == "completed", d["contract"]["status"])
    check("report overall = Follow-up required", d["report"]["overall_status"] == "Follow-up required", str(d["report"]))
    sec_map = {s["section"]: s["n"] for s in d["sections"]}
    check("A-G sections present", set(sec_map) == set("ABCDEFG"), str(sec_map))
    check("section row counts match", sum(sec_map.values()) == 82, str(sec_map))

    a = c.get(f"/api/contracts/{CONTRACT}/items", params={"section": "A"}).json()
    a6 = next(i for i in a["items"] if i["code"] == "A6")
    check("A6 easement A881068 searchable", "A881068" in str(a6["contents_text"]), str(a6["pages"])[:80])

    rep = c.get(f"/api/contracts/{CONTRACT}/report")
    check("report PDF downloadable", rep.status_code == 200 and rep.content[:5] == b"%PDF-",
          f"{rep.status_code} {len(rep.content)} bytes")
    print(f"\nALL {ok} M2 CHECKS PASSED")


if __name__ == "__main__":
    main()
