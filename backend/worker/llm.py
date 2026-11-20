"""OpenAI-compatible LLM client for contract extraction (M2 / S3).

Provider-agnostic: set LLM_BASE_URL + LLM_API_KEY + LLM_MODEL in the project .env.
Works with 火山方舟 ARK, 阿里百炼, OpenAI, or any local OpenAI-compatible server.

No `openai` package needed -- uses stdlib urllib so it runs in the default env.
If LLM_API_KEY is empty, is_configured() returns False and the Worker degrades
gracefully (jobs -> awaiting_llm) instead of crashing.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import CFG, server_conn  # noqa: E402

# .env values are only a fallback/last-resort seed. The live config lives in
# the admin-editable llm_config DB row, read fresh on every call (hot reload).
_ENV_BASE = (CFG.get("LLM_BASE_URL") or "").strip().rstrip("/")
_ENV_KEY = (CFG.get("LLM_API_KEY") or "").strip()
_ENV_MODEL = (CFG.get("LLM_MODEL") or "").strip()
_ENV_VISION = (CFG.get("LLM_MODEL_VISION") or "").strip()
TIMEOUT = int(CFG.get("LLM_TIMEOUT", "120"))
MAX_TOKENS = int(CFG.get("LLM_MAX_TOKENS", "8192"))


def _load_config() -> dict:
    """Read the live admin-editable config from the DB; fall back to .env."""
    url = key = model = vision = ""
    timeout, max_tokens, enabled = TIMEOUT, MAX_TOKENS, True
    version = None
    try:
        with server_conn() as conn:
            row = conn.execute(
                "SELECT base_url,api_key,model,model_vision,timeout,max_tokens,enabled,updated_at "
                "FROM llm_config WHERE id=1").fetchone()
        if row:
            url, key, model, vision = (str(row[0] or "").strip().rstrip("/"),
                                       str(row[1] or "").strip(), str(row[2] or "").strip(),
                                       str(row[3] or "").strip())
            timeout = int(row[4] or TIMEOUT)
            max_tokens = int(row[5] or MAX_TOKENS)
            enabled = bool(row[6])
            version = row[7].isoformat() if row[7] else None
    except Exception:
        # Table may not exist yet on a fresh install -> use .env fallback.
        pass
    if not (url or model or key):
        url, key, model, vision = _ENV_BASE, _ENV_KEY, _ENV_MODEL, _ENV_VISION
    return {"base_url": url, "api_key": key, "model": model, "model_vision": vision,
            "timeout": timeout, "max_tokens": max_tokens, "enabled": enabled,
            "version": version}

# Config is a SYSTEM parameter: the admin saves it once and it applies to every
# later job without a restart. A job freezes its own copy at start so a save
# during a long analysis cannot switch models halfway through one report.
_JOB_CFG: dict | None = None


def begin_job_config() -> dict:
    global _JOB_CFG
    _JOB_CFG = _load_config()
    return dict(_JOB_CFG)


def end_job_config() -> None:
    global _JOB_CFG
    _JOB_CFG = None


def get_config() -> dict:
    if _JOB_CFG is not None:
        return dict(_JOB_CFG)
    return _load_config()


def is_configured() -> bool:
    c = get_config()
    return bool(c["enabled"] and c["base_url"] and c["api_key"] and c["model"])


def config_summary() -> dict:
    c = get_config()
    return {
        "configured": is_configured(),
        "base_url": c["base_url"] or None,
        "model": c["model"] or None,
        "model_vision": c["model_vision"] or None,
        "source": "db",
        "note": "" if is_configured() else "管理员尚未在 App 中配置大模型（或配置被停用），Worker 将进入离线降级模式",
    }


# Backwards-compatible module attributes (re-resolved per call where relevant).
def _cfg():
    return get_config()


def _chat(messages: list[dict], model: str | None = None, temperature: float = 0.0,
          max_tokens: int | None = None, timeout: int | None = None,
          attempts: int = 3) -> str:
    """One completion call, retried on transport failures.

    A single dropped read used to fail the whole report after 120s of work: one
    TimeoutError and the contract went straight to "failed". Transport errors are
    transient far more often than they are fatal, so they are retried with
    backoff. Deterministic errors (4xx other than 408/429) are not retried —
    retrying a bad key or a malformed request only wastes minutes.
    """
    c = get_config()
    if not is_configured():
        raise RuntimeError("LLM not configured (admin has not set API key/model/base_url)")
    url = c["base_url"] + "/chat/completions"
    payload = {
        "model": model or c["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens or c["max_tokens"],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + c["api_key"],
    })
    delay = 3.0
    last_err: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout or c["timeout"]) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            if e.code < 500 and e.code not in (408, 429):
                raise RuntimeError(f"LLM HTTP {e.code}: {detail}") from e
            last_err = RuntimeError(f"LLM HTTP {e.code}: {detail}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            reason = getattr(e, "reason", None) or str(e)
            last_err = RuntimeError(f"LLM request failed: {reason}")
        if attempt < attempts:
            print(f"[llm] attempt {attempt}/{attempts} failed ({last_err}); "
                  f"retrying in {delay:.0f}s", file=sys.stderr, flush=True)
            time.sleep(delay)
            delay *= 2.5
    else:
        raise last_err or RuntimeError("LLM request failed")
    try:
        return body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Unexpected LLM response: {str(body)[:300]}") from e


def _strip_fence(text: str) -> str:
    """Remove ```json ... ``` wrappers if the model added them."""
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S | re.I)
    return m.group(1).strip() if m else text


def _first_json(text: str):
    """Parse the first JSON object/array found in a string."""
    text = _strip_fence(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Find first {...} or [...] with balanced braces.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start < 0:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError("No valid JSON found in LLM output: " + text[:200])


def extract_json(messages: list[dict], **kwargs) -> dict:
    """Call the LLM and parse its reply as JSON. Retries once with a repair prompt."""
    raw = _chat(messages, **kwargs)
    try:
        return _first_json(raw)
    except ValueError:
        repair = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "Your reply was not valid JSON. Return ONLY the corrected JSON object, nothing else."},
        ]
        raw2 = _chat(repair, **kwargs)
        return _first_json(raw2)


def extract_json_vision(text_prompt: str, images_b64: list[str], **kwargs) -> dict:
    """Call a vision-capable model with page images (for image-only PDF pages)."""
    model = get_config()["model_vision"] or get_config()["model"]
    content: list[dict] = [{"type": "text", "text": text_prompt}]
    for b64 in images_b64:
        content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}})
    return extract_json([{"role": "user", "content": content}], model=model, **kwargs)


def test_connection() -> dict:
    """Send a tiny chat request with the current config; report success/failure."""
    if not is_configured():
        return {"ok": False, "error": config_summary()["note"]}
    try:
        reply = _chat([{"role": "user", "content": "Reply with exactly: OK"}],
                      max_tokens=16, timeout=min(30, get_config()["timeout"]))
        return {"ok": True, "reply": reply[:80], "model": get_config()["model"]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:400]}


if __name__ == "__main__":
    print(json.dumps(config_summary(), ensure_ascii=False, indent=2))
