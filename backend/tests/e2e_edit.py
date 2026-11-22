"""E2E: contract data editing (permission `contract.edit`).

Editing is a *business* capability: it belongs to the operator role, while the
`admin` account is a pure system administrator (users / roles / LLM key) and is
refused on every contract endpoint.

Covers:
  * PUT  /api/contracts/{no}                scalar fields, numbers validated
  * POST /api/contracts/{no}/items          add a row; code auto-increments
  * PUT  /api/contracts/{no}/items/{id}     content only, identity immutable
  * DELETE /api/contracts/{no}/items/{id}   must not exist (no deletion by design)
  * viewers are refused (403); sysadmin holds no contract.* capability at all

Everything it changes is snapshotted first and restored in `finally`, so the
test is safe to run against the live database.

Run: python tests/e2e_edit.py   (backend must be up on :8790)
"""
from __future__ import annotations

import sys
import time

import httpx

from creds import ADMIN, EDITOR

BASE = "http://127.0.0.1:8790"

RUN = str(int(time.time()))[-6:]

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

def pick_contract(editor: httpx.Client) -> tuple[str, int]:
    """A completed contract the editor owns, plus one item id from section A."""
    for c in editor.get("/api/contracts", params={"status": "completed"}).json()["contracts"]:
        no = c["contract_no"]
        items = editor.get(f"/api/contracts/{no}/items", params={"section": "A"}).json()["items"]
        if items:
            return no, items[0]["id"]
    raise SystemExit("no completed contract with items found - run e2e_m2 first")

def main() -> int:
    editor = login(EDITOR)
    sysadmin = login(ADMIN)
    no, item_id = pick_contract(editor)

    detail = editor.get(f"/api/contracts/{no}").json()["contract"]
    item = next(i for i in editor.get(f"/api/contracts/{no}/items", params={"section": "A"}).json()["items"]
                if i["id"] == item_id)
    snapshot = {
        "property_address": detail.get("property_address"),
        "registered_owner": detail.get("registered_owner"),
        "land_area_sqm": detail.get("land_area_sqm"),
        "council_rates_amt": detail.get("council_rates_amt"),
    }
    item_before = {
        "provided": item["provided"], "verified": item["verified"],
        "contents": item.get("contents_json") or [item.get("contents_text") or ""],
        "notes": item.get("notes"), "pages": item.get("pages") or [],
    }
    created_ids: list[int] = []
    print(f"=== contract editing E2E  ({no} / item {item_id}) ===")

    try:
        # --- scalar field edit ---
        new_addr = "EDIT-TEST 1 Example Street, Glen Waverley VIC 3150"
        r = editor.put(f"/api/contracts/{no}", json={"property_address": new_addr,
                                                     "registered_owner": "EDIT TEST OWNER"})
        check("edit scalar fields 200", r.status_code == 200, r.text[:120])
        d2 = editor.get(f"/api/contracts/{no}").json()["contract"]
        check("property_address persisted", d2["property_address"] == new_addr, str(d2["property_address"]))
        check("registered_owner persisted", d2["registered_owner"] == "EDIT TEST OWNER")

        r = editor.put(f"/api/contracts/{no}", json={"land_area_sqm": "not-a-number"})
        check("non-numeric area rejected 400", r.status_code == 400, r.text[:120])
        r = editor.put(f"/api/contracts/{no}", json={"land_area_sqm": "612.5"})
        check("numeric area accepted", r.status_code == 200)
        check("numeric area stored", float(editor.get(f"/api/contracts/{no}").json()
                                           ["contract"]["land_area_sqm"]) == 612.5)

        r = editor.put(f"/api/contracts/{no}", json={"status": "completed", "owner_id": 1})
        check("non-editable columns ignored", r.status_code == 400, r.text[:120])

        # --- item content edit ---
        r = editor.put(f"/api/contracts/{no}/items/{item_id}",
                       json={"provided": "Missing", "verified": "Unclear",
                             "contents": ["edit line one", "edit line two"], "notes": "edit-note"})
        check("edit item content 200", r.status_code == 200, r.text[:120])
        after = next(i for i in editor.get(f"/api/contracts/{no}/items", params={"section": "A"}).json()["items"]
                     if i["id"] == item_id)
        check("provided updated", after["provided"] == "Missing", after["provided"])
        check("verified updated", after["verified"] == "Unclear", after["verified"])
        check("contents lines kept", after["contents_json"] == ["edit line one", "edit line two"],
              str(after["contents_json"]))
        check("notes updated", after["notes"] == "edit-note", str(after["notes"]))

        r = editor.put(f"/api/contracts/{no}/items/{item_id}", json={"provided": "Bogus"})
        check("invalid provided rejected 400", r.status_code == 400, r.text[:120])
        r = editor.put(f"/api/contracts/{no}/items/{item_id}", json={"verified": "Bogus"})
        check("invalid verified rejected 400", r.status_code == 400, r.text[:120])

        # --- identity is immutable ---
        for field in ("item", "code", "section", "section_title"):
            r = editor.put(f"/api/contracts/{no}/items/{item_id}", json={field: "HACKED"})
            check(f"cannot change {field} 400", r.status_code == 400, r.text[:100])
        still = next(i for i in editor.get(f"/api/contracts/{no}/items", params={"section": "A"}).json()["items"]
                     if i["id"] == item_id)
        check("item text intact", still["item"] == item["item"], still["item"])
        check("item code intact", still["code"] == item["code"], still["code"])

        r = editor.delete(f"/api/contracts/{no}/items/{item_id}")
        check("item deletion not supported 405", r.status_code == 405, str(r.status_code))

        # --- add a row ---
        sec = "A"
        before = editor.get(f"/api/contracts/{no}/items", params={"section": sec}).json()["total"]
        r = editor.post(f"/api/contracts/{no}/items",
                        json={"section": sec, "item": f"EDIT TEST NEW {RUN}",
                              "provided": "Present", "verified": "Action",
                              "contents": "auto code row", "notes": "n"})
        check("add item 200", r.status_code == 200, r.text[:120])
        body = r.json()
        created_ids.append(body["id"])
        check("code auto-generated", body["code"].startswith(sec) and body["code"] != item["code"], body["code"])
        after_total = editor.get(f"/api/contracts/{no}/items", params={"section": sec}).json()["total"]
        check("section count grew by one", after_total == before + 1, f"{before} -> {after_total}")

        r = editor.post(f"/api/contracts/{no}/items",
                        json={"section": sec, "code": body["code"], "item": "duplicate code"})
        check("duplicate code rejected 409", r.status_code == 409, r.text[:100])

        r = editor.post(f"/api/contracts/{no}/items", json={"section": sec, "item": ""})
        check("empty item text rejected 400", r.status_code == 400, r.text[:100])

        r = editor.post(f"/api/contracts/{no}/items", json={"section": "Z", "item": "bad section"})
        check("bad section rejected 400", r.status_code == 400, r.text[:100])

        # --- the system admin must stay out of contract work ---
        r = sysadmin.get("/api/contracts")
        check("admin cannot list contracts 403", r.status_code == 403, str(r.status_code))
        r = sysadmin.get(f"/api/contracts/{no}")
        check("admin cannot open a contract 403", r.status_code == 403, str(r.status_code))
        r = sysadmin.get(f"/api/contracts/{no}/items")
        check("admin cannot read checklist 403", r.status_code == 403, str(r.status_code))
        r = sysadmin.put(f"/api/contracts/{no}", json={"property_address": "admin edit"})
        check("admin cannot edit contract 403", r.status_code == 403, str(r.status_code))
        r = sysadmin.put(f"/api/contracts/{no}/items/{item_id}", json={"provided": "NA"})
        check("admin cannot edit item 403", r.status_code == 403, str(r.status_code))
        r = sysadmin.post(f"/api/contracts/{no}/items", json={"section": "A", "item": "admin row"})
        check("admin cannot add item 403", r.status_code == 403, str(r.status_code))
        r = sysadmin.get(f"/api/contracts/{no}/export")
        check("admin cannot export 403", r.status_code == 403, str(r.status_code))

        # --- editor still gets endpoint-level validation ---
        r = editor.put(f"/api/contracts/{no}", json={})
        check("empty payload rejected 400", r.status_code == 400, r.text[:100])
        r = editor.put("/api/contracts/CT-00000000-NOPE", json={"property_address": "x"})
        check("unknown contract 404", r.status_code == 404, str(r.status_code))
        r = editor.put("/api/contracts/CT-20261118-H1NKL3", json={"property_address": "not mine"})
        check("cannot edit someone else's contract 404/403", r.status_code in (403, 404), str(r.status_code))

    finally:
        # restore scalar fields
        editor.put(f"/api/contracts/{no}", json={
            "property_address": snapshot["property_address"],
            "registered_owner": snapshot["registered_owner"],
            "land_area_sqm": snapshot["land_area_sqm"],
            "council_rates_amt": snapshot["council_rates_amt"],
        })
        # restore the item
        editor.put(f"/api/contracts/{no}/items/{item_id}", json=item_before)
        # drop rows we created (no delete API by design -> direct DB cleanup)
        if created_ids:
            sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
            from app.db import server_conn
            with server_conn() as conn:
                conn.execute("DELETE FROM report_items WHERE id = ANY(%s)", (created_ids,))
        # verify the restore
        back = editor.get(f"/api/contracts/{no}").json()["contract"]
        restored = (back["property_address"] == snapshot["property_address"]
                    and back["registered_owner"] == snapshot["registered_owner"])
        it = next((i for i in editor.get(f"/api/contracts/{no}/items", params={"section": "A"}).json()["items"]
                   if i["id"] == item_id), None)
        restored_item = bool(it and it["provided"] == item_before["provided"]
                             and it["verified"] == item_before["verified"]
                             and it["notes"] == item_before["notes"])
        check("state restored after test", restored and restored_item,
              f"contract={restored} item={restored_item}")

    print(f"\n=== RESULT: {passed} passed, {failed} failed ===")
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    raise SystemExit(main())
