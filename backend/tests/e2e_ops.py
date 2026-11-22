"""E2E: operations layer (search / retry / delete / progress fields).

Covers the features added in 0.5.0-ops:
  * GET /api/contracts?q=      fuzzy search across contract_no/address/owner/lot
  * POST /api/contracts/{no}/retry   re-queue (idempotent, permission-gated)
  * DELETE /api/contracts/{no}       owner-only, cascades DB rows + disk files
  * job progress fields (stage/stage_msg/stage_at) exposed in the detail payload

Run: python tests/e2e_ops.py   (backend must be up on :8790)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

from creds import ADMIN, NEW_USER_PASSWORD

BASE = "http://127.0.0.1:8790"

RUN = str(int(time.time()))[-6:]
PDF_DIR = Path("D:/xinxin/contract/_work/ops_test")

passed = failed = 0

def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")

def make_pdf(path: Path, marker: str) -> Path:
    """Create a tiny real PDF so upload/parse have something valid."""
    import pymupdf

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    for i in range(2):
        page = doc.new_page()
        page.insert_text((72, 72), f"OPS TEST {marker} page {i + 1}", fontsize=14)
    doc.save(str(path))
    doc.close()
    return path

def login(username: str, password: str) -> httpx.Client:
    c = httpx.Client(base_url=BASE, timeout=60)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, (username, r.status_code, r.text)
    return c

def make_user(admin: httpx.Client, name: str, role: str) -> httpx.Client:
    admin.post("/api/auth/register", json={
        "username": name, "password": NEW_USER_PASSWORD, "display_name": name, "role": role})
    pend = admin.get("/api/admin/pending-users").json()["users"]
    uid = next((u["uid"] for u in pend if u["username"] == name), None)
    assert uid, f"pending user {name} not found"
    admin.post(f"/api/admin/users/{uid}/approve", json={"role": role})
    return login(name, NEW_USER_PASSWORD)

def main() -> int:
    admin = login(ADMIN["username"], ADMIN["password"])
    print("[1] permission boundaries")
    viewer = make_user(admin, f"ops.viewer.{RUN}", "viewer")
    operator = make_user(admin, f"ops.op.{RUN}", "operator")

    pdf = make_pdf(PDF_DIR / f"ops_{RUN}.pdf", RUN)
    r = viewer.post("/api/contracts", files={"file": ("x.pdf", pdf.read_bytes(), "application/pdf")})
    check("viewer cannot upload (403)", r.status_code == 403, f"got {r.status_code}")

    print("[2] upload + progress payload")
    r = operator.post("/api/contracts", files={"file": ("ops.pdf", pdf.read_bytes(), "application/pdf")})
    check("operator upload 200", r.status_code == 200, r.text[:120])
    no = r.json()["contract_no"]
    d = operator.get(f"/api/contracts/{no}").json()
    check("new contract is queued", d["contract"]["status"] == "queued", d["contract"]["status"])
    job = d["jobs"][0]
    check("detail exposes stage fields",
          all(k in job for k in ("stage", "stage_msg", "stage_at")), str(list(job)))
    check("detail exposes error_msg", "error_msg" in job, str(list(job)))

    print("[3] search")
    real = operator.get("/api/contracts").json()["contracts"]
    check("search by contract no", any(
        x["contract_no"] == no for x in operator.get("/api/contracts", params={"q": no}).json()["contracts"]))
    check("search miss returns empty",
          operator.get("/api/contracts", params={"q": "ZZZ-NO-SUCH-" + RUN}).json()["total"] == 0)
    check("search + status filter combine",
          operator.get("/api/contracts", params={"q": no, "status": "completed"}).json()["total"] == 0)
    check("list returns a total field", "total" in operator.get("/api/contracts").json())

    print("[4] retry")
    check("viewer cannot retry (403)",
          viewer.post(f"/api/contracts/{no}/retry").status_code == 403)
    r = operator.post(f"/api/contracts/{no}/retry")
    check("retry on queued is idempotent", r.status_code == 200 and r.json().get("already") is True, r.text[:120])
    jobs_now = operator.get(f"/api/contracts/{no}").json()["jobs"]
    check("retry did not stack duplicate jobs", len(jobs_now) == 1, f"{len(jobs_now)} jobs")
    check("retry on unknown contract 404",
          operator.post("/api/contracts/CT-NOPE-000000/retry").status_code == 404)

    print("[5] delete")
    check("viewer cannot delete (403)",
          viewer.delete(f"/api/contracts/{no}").status_code == 403)
    outsider = make_user(admin, f"ops.out.{RUN}", "operator")
    bad = outsider.delete(f"/api/contracts/{no}").status_code
    check("non-owner delete is rejected", bad in (403, 404), f"got {bad}")
    check("contract visible before delete",
          operator.get(f"/api/contracts/{no}").json()["contract"]["contract_no"] == no)

    r = operator.delete(f"/api/contracts/{no}")
    check("owner delete 200", r.status_code == 200, r.text[:160])
    check("delete removed disk files", all(
        not Path(p).exists() for p in r.json().get("files", [])), str(r.json().get("files")))
    check("detail 404 after delete", operator.get(f"/api/contracts/{no}").status_code == 404)
    left = [x["contract_no"] for x in operator.get("/api/contracts").json()["contracts"]]
    check("contract gone from list", no not in left, str(left))

    print("[6] the system admin stays out of contract maintenance")
    out_pdf = make_pdf(PDF_DIR / f"out_{RUN}.pdf", "OUT" + RUN)
    r = outsider.post("/api/contracts", files={"file": ("o.pdf", out_pdf.read_bytes(), "application/pdf")})
    ano = r.json()["contract_no"]
    check("outsider upload 200", r.status_code == 200, r.text[:120])
    check("admin cannot read another user's contract",
          admin.get(f"/api/contracts/{ano}").status_code == 403)
    check("admin cannot delete another user's contract",
          admin.delete(f"/api/contracts/{ano}").status_code == 403)
    check("admin cannot list contracts", admin.get("/api/contracts").status_code == 403)
    check("owner still deletes their own contract",
          outsider.delete(f"/api/contracts/{ano}").status_code == 200)

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
