"""E2E: workbench tabs — cross-contract follow-ups, CSV export, global operation log.

Covers:
  * GET /api/followups                  flattened worklist + keyword filter + RLS isolation
  * GET /api/contracts/{no}/export      checklist / follow-ups CSV (BOM, 409 when no report)
  * GET /api/admin/audit                global log (admin.audit), action filter, keyword search

Read-only: it never mutates data.

Run: python tests/e2e_workbench.py   (backend must be up on :8790)
"""
from __future__ import annotations

import csv
import io

import httpx

from creds import ADMIN, MEMBER

BASE = "http://127.0.0.1:8790"

passed = failed = 0

def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")

def login(creds: dict) -> httpx.Client:
    c = httpx.Client(base_url=BASE, timeout=60)
    r = c.post("/api/auth/login", json=creds)
    assert r.status_code == 200, (creds["username"], r.status_code, r.text)
    return c

def main() -> int:
    admin = login(ADMIN)
    member = login(MEMBER)
    editor = member            # same business account, named for clarity

    # ---- cross-contract follow-up worklist ----
    r = editor.get("/api/followups")
    check("followups 200", r.status_code == 200, r.text[:120])
    d = r.json()
    items = d.get("items") or []
    check("has follow-up items", d.get("total", 0) > 0, str(d.get("total")))
    check("counts distinct contracts", d.get("contracts", 0) >= 1, str(d.get("contracts")))
    check("total matches item count", d.get("total") == len(items))
    if items:
        f = items[0]
        check("item shape", all(k in f for k in
              ("contract_no", "address", "status", "version", "matter", "finding", "action", "pages")),
              str(sorted(f)))
        check("finding/action are lists",
              isinstance(f["finding"], list) and isinstance(f["action"], list))
        check("matter not empty", bool(f["matter"]))

    first_matter = items[0]["matter"].split()[0] if items else ""
    if first_matter:
        rq = editor.get("/api/followups", params={"q": first_matter})
        check("keyword narrows the list",
              rq.status_code == 200 and 0 < rq.json()["total"] <= d["total"],
              str(rq.json().get("total")))
    rq = editor.get("/api/followups", params={"q": "zzz-no-such-matter"})
    check("no-hit keyword returns empty", rq.json().get("total") == 0)
    if items:
        one = editor.get("/api/followups", params={"contract_no": items[0]["contract_no"]}).json()
        check("contract filter scopes to one contract",
              one["contracts"] == 1 and one["total"] > 0, str(one["contracts"]))

    # RLS: the worklist only ever contains contracts this account can see
    mine = {x["contract_no"] for x in items}
    own = {c["contract_no"] for c in
           editor.get("/api/contracts", params={"limit": 200}).json()["contracts"]}
    check("worklist stays inside the user's own contracts (RLS)", mine <= own, f"{mine} vs {own}")
    check("every item carries a contract no", all(x["contract_no"] for x in items))
    anon = httpx.Client(base_url=BASE, timeout=30).get("/api/followups")
    check("anonymous gets 401", anon.status_code == 401, str(anon.status_code))
    # the system administrator has no contract.view, so no worklist either
    r = admin.get("/api/followups")
    check("admin cannot open the worklist 403", r.status_code == 403, str(r.status_code))
    r = admin.get("/api/contracts", params={"status": "completed"})
    check("admin cannot list contracts 403", r.status_code == 403, str(r.status_code))

    # ---- CSV export ----
    completed = [c["contract_no"] for c in
                 editor.get("/api/contracts", params={"status": "completed"}).json()["contracts"]]
    check("have a completed contract to export", bool(completed))
    no = completed[0] if completed else None
    if no:
        r = editor.get(f"/api/contracts/{no}/export", params={"what": "items"})
        check("checklist export 200", r.status_code == 200, r.text[:100])
        body = r.content
        check("csv has utf-8 BOM", body.startswith(b"\xef\xbb\xbf"), body[:3].hex())
        check("csv content-type", "text/csv" in r.headers.get("content-type", ""))
        cd = r.headers.get("content-disposition", "")
        check("filename carries contract no", no in cd and "_checklist.csv" in cd, cd)
        text = body.decode("utf-8-sig")
        rows = [ln for ln in text.splitlines() if ln.strip()]
        check("csv has section rows", len(rows) > 30, str(len(rows)))
        check("csv header columns", "Section,Section title,Code,Item,Provided,Verified" in text)
        parsed = list(csv.reader(io.StringIO(text)))
        body_rows = [r for r in parsed[4:] if r and r[0]]
        secs = {r[0] for r in body_rows}
        check("csv keeps every A-G section", secs == set("ABCDEFG"), str(sorted(secs)))
        check("csv rows carry code + item",
              all(len(r) >= 4 and r[2] and r[3] for r in body_rows), str(len(body_rows)))
        check("csv escapes embedded quotes",
              ('"' in text) or ("\n" not in text), "quoted field expected")

        rf = editor.get(f"/api/contracts/{no}/export", params={"what": "followups"})
        check("follow-ups export 200", rf.status_code == 200, rf.text[:80])
        tf = rf.content.decode("utf-8-sig")
        check("follow-ups csv header", "No,Matter,Findings,Actions,Pages" in tf)
        check("follow-ups rows present", len([ln for ln in tf.splitlines() if ln.strip()]) > 3)

        rb = editor.get(f"/api/contracts/{no}/export", params={"what": "bogus"})
        check("unknown export kind 400", rb.status_code == 400, str(rb.status_code))
        r404 = editor.get("/api/contracts/CT-NOPE-0000/export")
        check("unknown contract 404", r404.status_code == 404, str(r404.status_code))

    pending = [c["contract_no"] for c in
               editor.get("/api/contracts", params={"limit": 200}).json()["contracts"]
               if c["status"] != "completed"]
    if pending:
        r = editor.get(f"/api/contracts/{pending[0]}/export")
        check("export without report 409", r.status_code == 409, str(r.status_code))

    # ---- global operation log ----
    r = admin.get("/api/admin/audit", params={"limit": 10})
    check("audit 200 for admin", r.status_code == 200, r.text[:100])
    a = r.json()
    check("audit returns logs", a["total"] > 0)
    check("audit returns action catalogue", len(a["actions"]) > 0, str(len(a["actions"])))
    if a["logs"]:
        lg = a["logs"][0]
        check("log shape", all(k in lg for k in
              ("id", "action", "target", "detail", "created_at", "actor")), str(sorted(lg)))
        check("actor resolved", bool(lg["actor"]))
        check("newest first", a["logs"][0]["id"] >= a["logs"][-1]["id"])
    act = a["actions"][0]
    ra = admin.get("/api/admin/audit", params={"action": act, "limit": 5})
    check("action filter works",
          ra.status_code == 200 and all(x["action"] == act for x in ra.json()["logs"]),
          act)
    rq = admin.get("/api/admin/audit", params={"q": "edit", "limit": 10})
    check("audit keyword search 200", rq.status_code == 200)
    check("audit keyword narrows", rq.json()["total"] <= a["total"] or a["total"] <= 10)

    r = member.get("/api/admin/audit")
    check("operator cannot read audit log 403", r.status_code == 403, str(r.status_code))
    r = member.get("/api/admin/audit", params={"limit": 5})
    check("operator still 403 with params", r.status_code == 403, str(r.status_code))

    # new permission point must be part of the catalogue
    perms = admin.get("/api/admin/permissions").json()["permissions"]
    codes = {p["code"] for p in perms}
    check("admin.audit permission exists", "admin.audit" in codes, str(sorted(codes)))
    me_admin = admin.get("/api/auth/me").json()["user"]
    check("admin carries admin.audit", "admin.audit" in me_admin["permissions"])
    me_member = member.get("/api/auth/me").json()["user"]
    check("operator lacks admin.audit", "admin.audit" not in me_member["permissions"])

    print(f"\n=== RESULT: {passed} passed, {failed} failed ===")
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    raise SystemExit(main())
