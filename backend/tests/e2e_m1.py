"""M1 end-to-end test: register -> gate -> approve -> upload -> dedup -> A-G query.

Run against the live server on 8790. Uses httpx with cookie persistence.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8790"
ADMIN = {"username": "admin", "password": "Zhouxin1975"}
# General account names (NOT 11-digit phones) - proves overseas-style accounts work.
MEMBER = {"username": "member.alpha", "password": "TestMember2026"}
PDF = Path("D:/downloads/合同/36 Hinkler Road, Glen Waverley_contract.pdf")

ok = 0
def check(label: str, cond: bool, detail: str = "") -> None:
    global ok
    ok += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f" :: {detail}" if detail else ""))
    if not cond:
        raise SystemExit(1)


def main() -> None:
    admin = httpx.Client(base_url=BASE, timeout=30)
    member = httpx.Client(base_url=BASE, timeout=30)

    r = admin.post("/api/auth/login", json=ADMIN)
    check("admin login", r.status_code == 200 and r.json()["user"]["role"] == "admin", r.text[:120])
    r = admin.get("/api/contracts")
    check("admin has no contract access", r.status_code == 403, f"HTTP {r.status_code}")
    r = admin.get("/api/auth/me")
    check("admin holds admin.* only", all(p.startswith("admin.") for p in r.json()["user"]["permissions"]),
          str(r.json()["user"]["permissions"]))

    # Register a fresh member (ignore if already registered from a prior run)
    r = member.post("/api/auth/register", json={**MEMBER, "display_name": "测试成员"})
    check("register (new or duplicate)", r.status_code in (200, 409), r.text[:120])

    r = member.post("/api/auth/login", json=MEMBER)
    check("member login (pending allowed)", r.status_code == 200, r.text[:120])
    uid = r.json()["user"]["uid"]
    status = r.json()["user"]["status"]

    if status == "pending":
        r = member.get("/api/contracts")
        check("pending user blocked from API", r.status_code == 403, f"HTTP {r.status_code}")

        r = admin.get("/api/admin/pending-users")
        check("admin sees pending list", any(u["uid"] == uid for u in r.json()["users"]), r.text[:160])

        r = admin.post(f"/api/admin/users/{uid}/approve")
        check("admin approves member", r.status_code == 200, r.text[:120])

        member.cookies.clear()
        r = member.post("/api/auth/login", json=MEMBER)
        check("re-login after approval", r.status_code == 200 and r.json()["user"]["status"] == "active", r.text[:120])

    r = member.get("/api/contracts")
    check("active member can list contracts", r.status_code == 200, r.text[:160])

    # Upload the real contract PDF
    with PDF.open("rb") as f:
        r = member.post("/api/contracts", files={"file": ("contract.pdf", f, "application/pdf")})
    check("upload PDF (first or previously uploaded)", r.status_code == 200, r.text[:200])
    contract_no = r.json()["contract_no"]
    print(f"       contract_no = {contract_no} (duplicate={r.json().get('duplicate')})")

    with PDF.open("rb") as f:
        r2 = member.post("/api/contracts", files={"file": ("contract.pdf", f, "application/pdf")})
    check("SHA-256 dedup returns same contract", r2.status_code == 200 and r2.json().get("duplicate")
          and r2.json()["contract_no"] == contract_no, r2.text[:160])

    r = member.get(f"/api/contracts/{contract_no}")
    check("contract detail", r.status_code == 200 and r.json()["contract"]["contract_no"] == contract_no, r.text[:160])

    r = member.get(f"/api/contracts/{contract_no}/items", params={"section": "Z"})
    check("invalid section rejected", r.status_code == 400, r.text[:120])

    # Checklist rows of the uploaded contract (same PDF, so the same A-G skeleton).
    # The admin account holds no contract.* permission, so every read below uses
    # the business account that owns the contract.
    r = member.get(f"/api/contracts/{contract_no}/items", params={"section": "A"})
    check("A-section items via API", r.status_code == 200 and r.json()["total"] == 11,
          f"total={r.json().get('total') if r.status_code==200 else r.text[:120]}")
    if r.status_code == 200 and r.json()["items"]:
        a6 = next((i for i in r.json()["items"] if i["code"] == "A6"), None)
        check("A6 content searchable", a6 is not None and "A881068" in str(a6.get("contents_text", "")),
              str(a6.get("contents_text", ""))[:100] if a6 else "A6 not found")

    r = member.get(f"/api/contracts/{contract_no}/items", params={"verified": "Action"})
    # verified filter is an exact match: Action=14, Unclear=4 (18 combined)
    check("exact verified=Action filter", r.status_code == 200 and r.json()["total"] == 14
          and all(i["verified"] == "Action" for i in r.json()["items"]),
          f"total={r.json().get('total') if r.status_code==200 else r.text[:120]}")

    r = member.get(f"/api/contracts/{contract_no}/original")
    check("original download", r.status_code == 200 and r.content[:5] == b"%PDF-", f"{r.status_code} {len(r.content)} bytes")

    # Visibility gate: private contracts stay invisible to other approved users.
    r = member.put(f"/api/contracts/{contract_no}/visibility", json={"visibility": "private"})
    check("owner can set private", r.status_code == 200 and r.json()["visibility"] == "private",
          f"{r.status_code} {r.text[:120]}")

    # Another APPROVED user must not see this PRIVATE contract
    other = httpx.Client(base_url=BASE, timeout=30)
    other_account = "other.beta"
    r = other.post("/api/auth/register", json={"username": other_account, "password": "TestOther2026", "display_name": "其他用户"})
    r = admin.get("/api/admin/pending-users")
    for u in r.json()["users"]:
        if u["username"] == other_account:
            admin.post(f"/api/admin/users/{u['uid']}/approve")
    other.post("/api/auth/login", json={"username": other_account, "password": "TestOther2026"})
    r = other.get(f"/api/contracts/{contract_no}")
    check("cross-user private contract hidden", r.status_code == 404, f"HTTP {r.status_code}")

    print(f"\nALL {ok} CHECKS PASSED")


if __name__ == "__main__":
    main()
