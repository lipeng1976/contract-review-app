"""E2E RBAC: roles, permissions, least-privilege gates, custom roles, hot reload.
Run against the live backend at http://127.0.0.1:8790 (admin must be admin/<password from TEST_ADMIN_PASSWORD>).
"""
import httpx
import time

from creds import ADMIN, NEW_USER_PASSWORD

BASE = "http://127.0.0.1:8790"
RUN = str(int(time.time()))[-6:]  # unique suffix so re-runs never collide

CUSTOM_ROLE = f"auditor_{RUN}"
PDF = "D:/downloads/合同/36 Hinkler Road, Glen Waverley_contract.pdf"

passed = failed = 0

def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK  {name}")
    else:
        failed += 1
        print(f"  FAIL {name}  {detail}")

def login(username, password):
    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    return c, r

def register_and_approve(admin, username, role):
    """Register a fresh account, approve it with `role`, return a logged-in client."""
    # Idempotent: if left over from a previous run, delete its roles and re-register.
    for u in admin.get("/api/admin/users").json()["users"]:
        if u["username"] == username:
            admin.put(f"/api/admin/users/{u['uid']}/roles", json={"roles": ["viewer"]})
    admin.post("/api/auth/register", json={
        "username": username, "password": NEW_USER_PASSWORD,
        "display_name": username, "role": role})
    pend = admin.get("/api/admin/pending-users").json()["users"]
    uid = next(u["uid"] for u in pend if u["username"] == username)
    admin.post(f"/api/admin/users/{uid}/approve", json={"role": role})
    c, r = login(username, NEW_USER_PASSWORD)
    return c, uid

def cleanup(admin, username):
    users = admin.get("/api/admin/users").json()["users"]
    for u in users:
        if u["username"] == username:
            # suspend to neutralize; roles table FKs cascade only on user delete,
            # so we just suspend (tests use unique names each run).
            admin.post(f"/api/admin/users/{u['uid']}/suspend")
            return u["uid"]
    return None

def main():
    global passed, failed
    print("=== RBAC E2E ===")
    admin, r = login(ADMIN["username"], ADMIN["password"])
    check("admin login 200", r.status_code == 200, r.text[:120])
    me = r.json()["user"]
    check("admin is_superuser", me.get("is_superuser") is True)
    # Derive the expected permission set from the catalog instead of hardcoding a
    # count, so adding a permission point does not silently break this test.
    perms = admin.get("/api/admin/permissions").json()["permissions"]
    ALL = {p["code"] for p in perms}
    # The admin account is a *system administrator*: permissions are taken
    # literally from role_permissions (no superuser bypass), so it must hold
    # exactly the admin.* points and nothing from the contract domain.
    ADMIN_ONLY = {c for c in ALL if c.startswith("admin.")}
    check("admin holds exactly the admin.* perms", set(me.get("permissions", [])) == ADMIN_ONLY,
          str(sorted(me.get("permissions"))))
    check("admin holds no contract capability",
          not any(c.startswith("contract.") for c in me.get("permissions", [])), str(me.get("permissions")))

    # --- catalogs ---
    roles = admin.get("/api/admin/roles").json()["roles"]
    check("preset roles admin/operator/viewer", {x["name"] for x in roles} >= {"admin", "operator", "viewer"})
    admin_role = next(x for x in roles if x["name"] == "admin")
    viewer_role = next(x for x in roles if x["name"] == "viewer")
    op_role = next(x for x in roles if x["name"] == "operator")
    check("admin role carries admin.* only", set(admin_role["permissions"]) == ADMIN_ONLY,
          str(sorted(admin_role["permissions"])))
    check("viewer has view+download only", set(viewer_role["permissions"]) == {"contract.view", "contract.download"})
    check("operator has upload", "contract.upload" in op_role["permissions"])
    # Contract maintenance belongs to the business roles, not the system admin.
    check("operator owns contract.edit", "contract.edit" in op_role["permissions"],
          str(sorted(op_role["permissions"])))
    check("viewer cannot edit contract", "contract.edit" not in viewer_role["permissions"])
    check("system roles flagged", all(x["is_system"] for x in (admin_role, viewer_role, op_role)))
    check("permission catalog complete", {"contract.view", "contract.upload", "contract.download",
                                          "contract.edit", "admin.users", "admin.roles",
                                          "admin.audit", "admin.llm"} <= ALL,
          str(sorted(ALL)))

    # --- non-admin cannot touch admin APIs ---
    viewer, vuid = register_and_approve(admin, f"rbac.viewer.{RUN}", "viewer")
    check("viewer login role=viewer", viewer.get("/api/auth/me").json()["user"]["roles"] == ["viewer"])
    for ep in ("/api/admin/roles", "/api/admin/permissions", "/api/admin/users", "/api/admin/llm-config", "/api/admin/pending-users"):
        check(f"viewer 403 on {ep}", viewer.get(ep).status_code == 403)

    # --- viewer cannot upload (contract.upload gate) ---
    with open(PDF, "rb") as f:
        vr = viewer.post("/api/contracts", files={"file": ("test.pdf", f, "application/pdf")})
    check("viewer upload 403", vr.status_code == 403, f"{vr.status_code} {vr.text[:120]}")
    check("viewer can list contracts", viewer.get("/api/contracts").status_code == 200)

    # --- operator can upload ---
    operator, ouid = register_and_approve(admin, f"rbac.operator.{RUN}", "operator")
    check("operator has upload perm", "contract.upload" in operator.get("/api/auth/me").json()["user"]["permissions"])
    with open(PDF, "rb") as f:
        our = operator.post("/api/contracts", files={"file": ("op.pdf", f, "application/pdf")})
    check("operator upload 200", our.status_code == 200, f"{our.status_code} {our.text[:120]}")

    # --- admin creates a custom role with view-only, assigns to a new user ---
    custom = admin.post("/api/admin/roles", json={
        "name": CUSTOM_ROLE, "label_zh": "审计员", "label_en": "Auditor",
        "description": "只读审计", "permissions": ["contract.view"]})
    check("create custom role 200", custom.status_code == 200, custom.text[:200])
    check("duplicate role name 409",
          admin.post("/api/admin/roles", json={
              "name": CUSTOM_ROLE, "label_zh": "x", "label_en": "x", "permissions": []}).status_code == 409)
    check("custom name rejects invalid",
          admin.post("/api/admin/roles", json={
              "name": "Bad Name", "label_zh": "x", "label_en": "x", "permissions": []}).status_code == 400)

    auditor, aud_uid = register_and_approve(admin, f"rbac.auditor.{RUN}", "viewer")
    role_list = admin.get("/api/admin/roles").json()["roles"]
    rid = next(x["id"] for x in role_list if x["name"] == CUSTOM_ROLE)
    ar = admin.put(f"/api/admin/users/{aud_uid}/roles", json={"roles": [CUSTOM_ROLE]})
    check("assign custom role 200", ar.status_code == 200, ar.text[:200])
    # hot reload: same client, next request sees new perms
    aud_me = auditor.get("/api/auth/me").json()["user"]
    check("hot-reloaded roles", aud_me["roles"] == [CUSTOM_ROLE], str(aud_me["roles"]))
    check("custom role only view perm", aud_me["permissions"] == ["contract.view"], str(aud_me["permissions"]))
    check("auditor upload 403", auditor.post("/api/contracts",
          files={"file": ("a.pdf", b"%PDF-1.4 test", "application/pdf")}).status_code in (400, 403))
    check("auditor can view", auditor.get("/api/contracts").status_code == 200)
    check("auditor cannot admin", auditor.get("/api/admin/roles").status_code == 403)

    # --- system role protection & escalation guard ---
    check("delete system role 400", admin.delete(f"/api/admin/roles/{viewer_role['id']}").status_code == 400)
    check("rename system role 400",
          admin.put(f"/api/admin/roles/{viewer_role['id']}", json={"name": "viewer2"}).status_code == 400)
    # non-superuser with admin.users cannot grant admin role (escalation guard)
    # use viewer's token? viewer lacks admin.users. Create a user with admin.users but not superuser.
    mgr, mgr_uid = register_and_approve(admin, f"rbac.mgr.{RUN}", "viewer")
    admin.put(f"/api/admin/users/{mgr_uid}/roles", json={"roles": ["operator"]})
    # operator has no admin.users -> 403 on role assignment
    check("operator cannot assign roles", mgr.put(f"/api/admin/users/{vuid}/roles",
          json={"roles": ["operator"]}).status_code == 403)

    # --- edit custom role perms -> hot reload to add upload ---
    er = admin.put(f"/api/admin/roles/{rid}", json={
        "permissions": ["contract.view", "contract.upload"]})
    check("edit custom role 200", er.status_code == 200, er.text[:200])
    aud_me2 = auditor.get("/api/auth/me").json()["user"]
    check("perm hot-reload adds upload", "contract.upload" in aud_me2["permissions"], str(aud_me2["permissions"]))

    # --- delete custom role cascades user_roles ---
    dr = admin.delete(f"/api/admin/roles/{rid}")
    check("delete custom role 200", dr.status_code == 200, dr.text[:200])
    aud_me3 = auditor.get("/api/auth/me").json()["user"]
    check("user loses deleted role", aud_me3["roles"] == [], str(aud_me3["roles"]))

    print(f"\n=== RESULT: {passed} passed, {failed} failed ===")
    if failed:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
