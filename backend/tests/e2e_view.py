"""E2E: viewing layer (detail KPIs, follow-ups, in-contract search, audit trail).

Covers:
  * GET /api/contracts/{no}            report.risk_count / followup_count / followups[]
  * GET /api/contracts/{no}/items?q=   whole-contract keyword search
  * GET /api/contracts/{no}/audit      edit history (editors only)
  * GET /api/admin/llm-config          system-parameter status (effective/source/updated_by)

Read-only: it never mutates data.

Run: python tests/e2e_view.py   (backend must be up on :8790)
"""
from __future__ import annotations

import httpx

BASE = "http://127.0.0.1:8790"
ADMIN = {"username": "admin", "password": "Zhouxin1975"}
MEMBER = {"username": "member.alpha", "password": "TestMember2026"}

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
    admin = login(ADMIN)          # system administrator: LLM config only
    editor = login(MEMBER)        # business role: reads/edits contracts
    no = None
    for c in editor.get("/api/contracts", params={"status": "completed"}).json()["contracts"]:
        if editor.get(f"/api/contracts/{c['contract_no']}/items", params={"q": "a"}).json()["total"]:
            no = c["contract_no"]
            break
    if not no:
        print("no completed contract found - run e2e_m2 first")
        return 1
    print(f"=== viewing E2E  ({no}) ===")

    # --- detail: KPIs and follow-ups ---
    d = editor.get(f"/api/contracts/{no}").json()
    rep = d["report"]
    check("detail has report", rep is not None)
    check("risk_count is int", isinstance(rep.get("risk_count"), int), str(rep.get("risk_count")))
    check("followup_count is int", isinstance(rep.get("followup_count"), int))
    check("followups is a list", isinstance(rep.get("followups"), list))
    check("followup_count matches list", rep["followup_count"] == len(rep["followups"]))
    if rep["followups"]:
        f = rep["followups"][0]
        check("followup has matter/finding/action", bool(f.get("matter")) and
              isinstance(f.get("finding"), list) and isinstance(f.get("action"), list), str(f)[:120])
    sections = {s["section"]: s["n"] for s in d["sections"]}
    all_items = editor.get(f"/api/contracts/{no}/items").json()
    check("section counts sum to total rows", sum(sections.values()) == all_items["total"],
          f"{sum(sections.values())} vs {all_items['total']}")

    # --- in-contract search ---
    hits = editor.get(f"/api/contracts/{no}/items", params={"q": "easement"}).json()
    check("search returns hits", hits["total"] > 0, str(hits["total"]))
    check("search matches item or contents", all(
        "easement" in ((i["item"] or "") + " " + (i["contents_text"] or "")).lower() for i in hits["items"]))
    check("search spans sections", len({i["section"] for i in hits["items"]}) >= 1)
    scoped = editor.get(f"/api/contracts/{no}/items", params={"q": "easement", "section": "G"}).json()
    check("search respects section filter", all(i["section"] == "G" for i in scoped["items"]), str(scoped["total"]))
    junk = editor.get(f"/api/contracts/{no}/items", params={"q": "zzz-no-such-token-zzz"}).json()
    check("no-match search returns empty", junk["total"] == 0, str(junk["total"]))

    # --- audit trail ---
    a = editor.get(f"/api/contracts/{no}/audit")
    check("audit 200", a.status_code == 200, a.text[:100])
    logs = a.json()["logs"]
    check("audit rows returned", isinstance(logs, list) and len(logs) > 0, str(len(logs)))
    if logs:
        check("audit row shaped", {"id", "action", "detail", "created_at", "actor"} <= set(logs[0]))

    # the edit history is part of contract maintenance -> operator has it,
    # the system administrator must not reach the contract at all
    r = admin.get(f"/api/contracts/{no}/audit")
    check("admin cannot read contract audit 403", r.status_code == 403, str(r.status_code))
    r = admin.get(f"/api/contracts/{no}")
    check("admin cannot open contract detail 403", r.status_code == 403, str(r.status_code))

    # --- llm config as system parameter ---
    c = admin.get("/api/admin/llm-config")
    check("llm-config 200", c.status_code == 200, c.text[:100])
    cfg = c.json()
    check("declares database source", cfg.get("source") == "database", str(cfg.get("source")))
    check("declares immediate refresh", cfg.get("refresh") == "immediate")
    check("effective flag is bool", isinstance(cfg.get("effective"), bool))
    check("effective requires a key", cfg["effective"] == bool(cfg["has_key"] and cfg["enabled"]
                                                              and cfg["base_url"] and cfg["model"]))
    check("records who updated", "updated_by" in cfg, str(cfg.keys()))
    r = editor.get("/api/admin/llm-config")
    check("operator cannot read llm config 403", r.status_code == 403, str(r.status_code))

    print(f"\n=== RESULT: {passed} passed, {failed} failed ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
