"""E2E: 合同可见性（shared / private）。

口径：
  * 上传可带 visibility，默认 shared；private 仅上传者可见。
  * 随时可改，但只有上传者（或超管）能改。
  * 共享合同对"有 contract.view 的人"可见，子表（reports / items）随父合同可见。

覆盖：
    POST /api/contracts                      visibility 落库 + 非法值 400
    GET  /api/contracts                      列表按可见性过滤（含 owner_name / is_mine）
    GET  /api/contracts/{no}                 私有 404、共享 200
    GET  /api/contracts/{no}/items           共享后子表随父可见（RLS 继承）
    PUT  /api/contracts/{no}/visibility      本人可改、他人 403、非法值 400
    无 contract.view 的账号（admin）          列表 403

会创建 1 条测试合同，跑完自行删除；共享用例借用已有合同并还原其可见性。

Run: python tests/e2e_visibility.py   (backend must be up on :8790)
"""
from __future__ import annotations

import secrets
import time

import httpx

BASE = "http://127.0.0.1:8790"
ADMIN = {"username": "admin", "password": "Zhouxin1975"}
OWNER = {"username": "member.alpha", "password": "TestMember2026"}

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


def make_viewer(admin: httpx.Client) -> httpx.Client:
    """注册 + 审批一个只读账号（viewer 只有 contract.view / download）。"""
    uname = f"vis.beta.{int(time.time()) % 1000000}"
    pw = "TestVis2026"
    anon = httpx.Client(base_url=BASE, timeout=60)
    r = anon.post("/api/auth/register", json={
        "username": uname, "password": pw, "display_name": "可见性测试用户", "role": "viewer"})
    assert r.status_code == 200, r.text
    pend = admin.get("/api/admin/pending-users").json()["users"]
    uid = next(u["uid"] for u in pend if u["username"] == uname)
    admin.post(f"/api/admin/users/{uid}/approve")
    c = login({"username": uname, "password": pw})
    return c


def main() -> int:
    admin = login(ADMIN)
    owner = login(OWNER)
    beta = make_viewer(admin)

    # ---------- 1. 上传：默认与显式 private ----------
    pdf = (b"%PDF-1.4\n% visibility e2e\n1 0 obj<</Type/Catalog>>endobj\n"
           b"trailer<</Root 1 0 R>>\n%%EOF\n") + secrets.token_bytes(16)
    r = owner.post("/api/contracts",
                   files={"file": ("visibility-test.pdf", pdf, "application/pdf")},
                   data={"visibility": "private"})
    check("upload private 200", r.status_code == 200, r.text[:160])
    no = r.json().get("contract_no", "")
    check("upload returns visibility", r.json().get("visibility") == "private", r.text[:160])

    try:
        # ---------- 2. 本人能看到，别人看不到 ----------
        r = owner.get("/api/contracts")
        mine = [c for c in r.json()["contracts"] if c["contract_no"] == no]
        check("owner list has it", len(mine) == 1, r.text[:160])
        check("owner sees private + is_mine",
              bool(mine) and mine[0]["visibility"] == "private" and mine[0]["is_mine"] is True,
              str(mine[:1]))

        r = beta.get("/api/contracts")
        check("private hidden from other user",
              all(c["contract_no"] != no for c in r.json()["contracts"]), r.text[:160])
        r = beta.get(f"/api/contracts/{no}")
        check("private detail 404 for other user", r.status_code == 404, str(r.status_code))

        # ---------- 3. 非法值 ----------
        r = owner.put(f"/api/contracts/{no}/visibility", json={"visibility": "public"})
        check("invalid visibility 400", r.status_code == 400, f"{r.status_code} {r.text[:100]}")

        # ---------- 4. 改成 shared ----------
        r = owner.put(f"/api/contracts/{no}/visibility", json={"visibility": "shared"})
        check("owner switch to shared", r.status_code == 200 and r.json()["visibility"] == "shared",
              f"{r.status_code} {r.text[:120]}")

        r = beta.get("/api/contracts")
        rows = [c for c in r.json()["contracts"] if c["contract_no"] == no]
        check("shared appears for other user", len(rows) == 1, r.text[:200])
        check("shared row marks not mine + owner name",
              bool(rows) and rows[0]["is_mine"] is False and bool(rows[0]["owner_name"]),
              str(rows[:1]))
        r = beta.get(f"/api/contracts/{no}")
        check("shared detail 200 for other user", r.status_code == 200, str(r.status_code))

        # ---------- 5. 别人能看但不能改 ----------
        r = beta.put(f"/api/contracts/{no}/visibility", json={"visibility": "private"})
        check("non-owner cannot change visibility", r.status_code == 403,
              f"{r.status_code} {r.text[:100]}")
        r = beta.put(f"/api/contracts/{no}", json={"property_address": "hacked"})
        check("non-owner cannot edit fields", r.status_code == 403,
              f"{r.status_code} {r.text[:100]}")

        # ---------- 6. 改回 private 立刻失效 ----------
        owner.put(f"/api/contracts/{no}/visibility", json={"visibility": "private"})
        r = beta.get(f"/api/contracts/{no}")
        check("back to private hides again", r.status_code == 404, str(r.status_code))

        # ---------- 7. 子表随父合同可见（借用一份已有报告的合同） ----------
        r = owner.get("/api/contracts")
        done = [c for c in r.json()["contracts"]
                if c["status"] == "completed" and c["report_version"]]
        if done:
            target = done[0]
            tno, orig = target["contract_no"], target["visibility"]
            try:
                owner.put(f"/api/contracts/{tno}/visibility", json={"visibility": "shared"})
                r_shared = beta.get(f"/api/contracts/{tno}/items?section=A")
                owner.put(f"/api/contracts/{tno}/visibility", json={"visibility": "private"})
                r_after = beta.get(f"/api/contracts/{tno}/items?section=A")
            finally:
                owner.put(f"/api/contracts/{tno}/visibility", json={"visibility": orig})
            check("items visible while shared", r_shared.status_code == 200,
                  f"{r_shared.status_code} {r_shared.text[:100]}")
            check("items hidden again after private", r_after.status_code in (403, 404),
                  str(r_after.status_code))
        else:
            print("  SKIP  no completed contract to test child-table inheritance")

        # ---------- 8. 没有 contract.view 的账号 ----------
        r = admin.get("/api/contracts")
        check("no contract.view -> list 403", r.status_code == 403, str(r.status_code))
    finally:
        # 清理：删掉测试合同，别给库里留垃圾
        r = owner.delete(f"/api/contracts/{no}")
        check("cleanup: test contract deleted", r.status_code in (200, 204, 404), str(r.status_code))

    print(f"\n{'ALL ' + str(passed) + ' CHECKS PASSED' if failed == 0 else str(failed) + ' FAILED'}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
