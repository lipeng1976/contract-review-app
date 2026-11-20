"""Test admin-editable LLM config: auth gate, masked key, update, worker hot-reload.

IMPORTANT: this test mutates the live llm_config row. It snapshots the real
config at start and restores it in a finally block, so running the suite never
destroys the owner's real API key.
"""
from __future__ import annotations

import sys

import httpx

sys.path.insert(0, ".")
from app.db import server_conn  # noqa: E402

BASE = "http://127.0.0.1:8790"
ADMIN = {"username": "admin", "password": "Zhouxin1975"}
MEMBER = {"username": "member.alpha", "password": "TestMember2026"}

ok = 0


def check(label, cond, detail=""):
    global ok
    ok += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f" :: {detail}" if detail else ""))
    if not cond:
        raise SystemExit(1)


def snapshot():
    with server_conn() as conn:
        r = conn.execute(
            """SELECT base_url,api_key,model,model_vision,timeout,max_tokens,enabled
               FROM llm_config WHERE id=1""").fetchone()
    return dict(zip(("base_url", "api_key", "model", "model_vision", "timeout", "max_tokens", "enabled"), r))


def restore(cfg):
    with server_conn() as conn:
        conn.execute(
            """UPDATE llm_config SET base_url=%s,api_key=%s,model=%s,model_vision=%s,
               timeout=%s,max_tokens=%s,enabled=%s,updated_at=now() WHERE id=1""",
            (cfg["base_url"], cfg["api_key"], cfg["model"], cfg["model_vision"],
             cfg["timeout"], cfg["max_tokens"], cfg["enabled"]))
    print(f"[INFO] real LLM config restored (key_len={len(cfg['api_key'])})")


def main():
    original = snapshot()
    admin = httpx.Client(base_url=BASE, timeout=30)
    member = httpx.Client(base_url=BASE, timeout=30)
    try:
        admin.post("/api/auth/login", json=ADMIN)
        member.post("/api/auth/login", json=MEMBER)

        # Non-admin must be blocked from reading/writing the LLM config.
        r = member.get("/api/admin/llm-config")
        check("member cannot read LLM config", r.status_code == 403, str(r.status_code))
        r = member.put("/api/admin/llm-config", json={"base_url": "http://evil"})
        check("member cannot write LLM config", r.status_code == 403, str(r.status_code))

        # Admin reads config: key must be masked, never returned in plaintext.
        r = admin.get("/api/admin/llm-config")
        check("admin reads LLM config", r.status_code == 200, r.text[:160])
        cfg = r.json()
        check("key field is masked (never plaintext)", "api_key" not in cfg and "api_key_masked" in cfg,
              str(list(cfg.keys())))

        # Save a fake key + config, confirm worker sees it live (hot reload).
        payload = {"base_url": "https://example.com/v3", "api_key": "sk-test-SECRET-1234",
                   "model": "test-model", "model_vision": "test-vision", "enabled": True}
        r = admin.put("/api/admin/llm-config", json=payload)
        check("admin saves LLM config", r.status_code == 200, r.text[:200])
        check("saved key masked in response", r.json().get("api_key_masked") == "****1234"
              and "api_key" not in r.json(), str(r.json()))

        from worker import llm
        c = llm.get_config()
        check("worker hot-reloads config from DB", c["api_key"] == "sk-test-SECRET-1234"
              and c["model"] == "test-model" and llm.is_configured(), str(c))
        check("config_summary marks configured", llm.config_summary()["configured"] is True)

        # Sending the masked value back must NOT overwrite the stored key.
        admin.put("/api/admin/llm-config", json={**payload, "api_key": "****1234"})
        c2 = llm.get_config()
        check("masked key does not overwrite", c2["api_key"] == "sk-test-SECRET-1234", c2["api_key"])

        # Clear key -> worker degrades (not configured) without crashing.
        r = admin.put("/api/admin/llm-config", json={**payload, "api_key": "__CLEAR__"})
        check("admin clears key", r.status_code == 200 and r.json().get("has_key") is False, r.text[:160])
        check("worker degrades when key cleared", llm.is_configured() is False)

        print(f"\nALL {ok} CHECKS PASSED")
    finally:
        restore(original)


if __name__ == "__main__":
    main()
