"""M2 Worker orchestrator: S0 claim -> S7 ingest.

Runs locally on the laptop. Default = continuous poll loop; use --once for a
single claim/attempt (handy for tests). Uses superuser DB connection because
the Worker is a trusted server process that advances job/contract/report state
which client RLS intentionally forbids.

Modes:
  python -m worker.runner --once            # real LLM extraction (needs key)
  python -m worker.runner --once --replay-model <model.json> --contract-no <no>
                                            # offline: skip S1-S3, replay a known
                                            # validated model through S4-S7
  python -m worker.runner                   # daemon loop
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import traceback
import uuid
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import CFG, db_status, server_conn  # noqa: E402
from app.paths import DATA_DIR, REPORTS_DIR, TMP_DIR, resolve_path, to_relative  # noqa: E402
from db.ingest_report import ingest_report as do_ingest  # noqa: E402
from . import buyer_tables, extract, llm, parse, render, validate  # noqa: E402

WORKER_ID = f"worker-{uuid.uuid4().hex[:8]}"
POLL = int(CFG.get("WORKER_POLL_SECONDS", "5"))
LEASE_MIN = int(CFG.get("WORKER_LEASE_MINUTES", "120"))


def heartbeat(status: str = "idle", job_id: int | None = None,
              contract_no: str | None = None, stage: str | None = None,
              stage_msg: str | None = None) -> None:
    """Refresh worker_heartbeats so the API can tell "queued, worker is alive"
    from "queued, nobody will ever pick this up".

    Best-effort: a failed heartbeat must never stop an analysis.
    """
    try:
        with server_conn() as conn:
            conn.execute(
                """INSERT INTO worker_heartbeats
                       (worker_id, host, pid, status, job_id, contract_no,
                        stage, stage_msg, last_seen)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now())
                   ON CONFLICT (worker_id) DO UPDATE
                      SET host=EXCLUDED.host, pid=EXCLUDED.pid,
                          status=EXCLUDED.status,
                          job_id=CASE WHEN EXCLUDED.status IN ('idle','stopped') THEN NULL
                                      ELSE coalesce(EXCLUDED.job_id, worker_heartbeats.job_id) END,
                          contract_no=CASE WHEN EXCLUDED.status IN ('idle','stopped') THEN NULL
                                           ELSE coalesce(EXCLUDED.contract_no, worker_heartbeats.contract_no) END,
                          stage=CASE WHEN EXCLUDED.status IN ('idle','stopped') THEN NULL
                                     ELSE coalesce(EXCLUDED.stage, worker_heartbeats.stage) END,
                          stage_msg=CASE WHEN EXCLUDED.status IN ('idle','stopped') THEN NULL
                                         ELSE coalesce(EXCLUDED.stage_msg, worker_heartbeats.stage_msg) END,
                          last_seen=now()""",
                (WORKER_ID, socket.gethostname(), os.getpid(), status, job_id,
                 contract_no, stage, (stage_msg or "")[:200] or None))
    except Exception as e:  # pragma: no cover - diagnostics only
        print(f"[{WORKER_ID}] heartbeat failed: {e}", file=sys.stderr, flush=True)


# Shared with the heartbeat thread. While a job runs, the poll loop sits inside
# process() for minutes at a time; without a background beat last_seen would go
# stale and the UI would call a perfectly healthy worker "offline" exactly when
# it is waiting on the model.
_HB = {"status": "idle", "job_id": None, "contract_no": None}
_HB_LOCK = threading.Lock()


def hb_state(status: str | None = None, job_id: int | None = None,
             contract_no: str | None = None) -> dict:
    with _HB_LOCK:
        if status is not None:
            _HB["status"] = status
            if status in ("idle", "stopped"):
                _HB["job_id"] = None
                _HB["contract_no"] = None
        if job_id is not None:
            _HB["job_id"] = job_id
        if contract_no is not None:
            _HB["contract_no"] = contract_no
        return dict(_HB)


def start_heartbeat_thread(interval: float = 10.0) -> threading.Thread:
    def loop() -> None:
        while True:
            time.sleep(interval)
            snap = dict(_HB)
            if snap["status"] == "stopped":
                continue
            # stage/stage_msg stay NULL here so the SQL coalesce keeps whatever
            # set_stage() last wrote: this beat only proves the process is alive.
            heartbeat(snap["status"], snap["job_id"], snap["contract_no"])

    th = threading.Thread(target=loop, name="heartbeat", daemon=True)
    th.start()
    return th


def audit(actor_uid, action, target_type, target_id, detail=None):
    with server_conn() as conn:
        conn.execute(
            "INSERT INTO audit_logs (actor_uid,action,target_type,target_id,detail) VALUES (%s,%s,%s,%s,%s)",
            (actor_uid, action, target_type, str(target_id),
             json.dumps(detail or {}, ensure_ascii=False)))


# ---------- S0 ----------
def claim_job(conn, target_contract: str | None = None):
    """Atomically claim one queued (or expired-lease / awaiting_llm) job.

    If target_contract is given, only claim that contract's job (SQL-level
    filter) so other queued jobs are left untouched for a later run.
    """
    # NOTE: wrap the whole OR group in parentheses. SQL AND binds tighter than
    # OR, so without this the contract_no filter would only apply to the last
    # branch and an unrelated 'awaiting_llm' job could be claimed.
    where = """WHERE ((status='queued')
                  OR (status='awaiting_llm')
                  OR (status='analyzing' AND lease_until IS NOT NULL AND lease_until < now()))"""
    params: list = []
    if target_contract:
        where += " AND contract_no=%s"
        params.append(target_contract)
    params += [LEASE_MIN, WORKER_ID]
    with conn.cursor() as cur:
        cur.execute(
            """WITH picked AS (
                   SELECT id FROM analysis_jobs
                   """ + where + """
                   ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1
               )
               UPDATE analysis_jobs j
                  SET status='analyzing',
                      started_at=now(),
                      lease_until=now() + (%s * interval '1 minute'),
                      attempt = CASE WHEN j.status='analyzing' THEN j.attempt+1 ELSE j.attempt END,
                      worker_id=%s, error_code=NULL, error_msg=NULL,
                      stage='S0', stage_msg=NULL, stage_at=now()
                 FROM picked WHERE j.id=picked.id
               RETURNING j.id, j.contract_no, j.attempt""",
            tuple(params))
        row = cur.fetchone()
        if row is None:
            return None
        job_id, contract_no, attempt = row
        cur.execute(
            """UPDATE contracts SET status='analyzing'
               WHERE contract_no=%s AND status<>'completed'""", (contract_no,))
        return job_id, contract_no, attempt


def finish_job(job_id, contract_no, status, error_code=None, error_msg=None):
    with server_conn() as conn:
        conn.execute(
            """UPDATE analysis_jobs
               SET status=%s, error_code=%s, error_msg=%s, finished_at=now(), lease_until=NULL
               WHERE id=%s""", (status, error_code, (error_msg or "")[:2000] or None, job_id))
        if status in ("completed", "failed", "awaiting_llm"):
            conn.execute("UPDATE contracts SET status=%s WHERE contract_no=%s", (status, contract_no))
    audit(None, "job_" + status, "contract", contract_no,
          {"job_id": job_id, "error_code": error_code})


def set_stage(job_id: int, stage: str, msg: str | None = None) -> None:
    """Record the current pipeline stage so the phone UI can show progress.

    Also refreshes the worker heartbeat in the same statement: a long LLM call
    (S3 waits up to minutes for the model) must keep looking alive, otherwise
    the UI would report "stalled" precisely while it is waiting on the model.

    Best-effort: a progress write must never kill the analysis itself.
    """
    hb_state(None, job_id)
    try:
        with server_conn() as conn:
            conn.execute(
                """WITH upd AS (
                       UPDATE analysis_jobs
                          SET stage=%s, stage_msg=%s, stage_at=now()
                        WHERE id=%s
                      RETURNING contract_no
                   )
                   INSERT INTO worker_heartbeats
                          (worker_id, host, pid, status, job_id, contract_no,
                           stage, stage_msg, last_seen)
                   SELECT %s,%s,%s,'busy',%s,contract_no,%s,%s,now() FROM upd
                   ON CONFLICT (worker_id) DO UPDATE
                      SET status='busy', job_id=EXCLUDED.job_id,
                          contract_no=EXCLUDED.contract_no, stage=EXCLUDED.stage,
                          stage_msg=EXCLUDED.stage_msg, last_seen=now()""",
                (stage, (msg or "")[:200] or None, job_id,
                 WORKER_ID, socket.gethostname(), os.getpid(),
                 job_id, stage, (msg or "")[:200] or None))
    except Exception as e:  # pragma: no cover - diagnostics only
        print(f"[{WORKER_ID}] set_stage({stage}) failed: {e}", file=sys.stderr, flush=True)


# ---------- pipeline ----------
def process(job_id: int, contract_no: str, attempt: int,
            replay_model: dict | None = None) -> dict:
    with server_conn() as conn:
        row = conn.execute(
            "SELECT source_path FROM contracts WHERE contract_no=%s", (contract_no,)).fetchone()
    if row is None or not row[0]:
        raise FileNotFoundError(f"original PDF missing for {contract_no}")
    src = resolve_path(row[0])
    if src is None or not src.exists():
        raise FileNotFoundError(f"original PDF missing for {contract_no}")

    # S1: parse
    set_stage(job_id, "S1", "parse")
    parsed = parse.parse_pdf(src, render_images=True)
    parse.write_text_cache(parsed, TMP_DIR / contract_no)
    page_info = {"pages": len(parsed.pages),
                 "image_only": [p.number for p in parsed.image_only_pages]}
    set_stage(job_id, "S1", f"{len(parsed.pages)}p")

    # S2/S3: extract
    if replay_model is not None:
        model = replay_model
        mode = "replay"
        set_stage(job_id, "S3", "replay")
    else:
        if not llm.is_configured():
            finish_job(job_id, contract_no, "awaiting_llm", "llm_not_configured",
                       "LLM_API_KEY/LLM_MODEL/LLM_BASE_URL 未配置")
            return {"status": "awaiting_llm", "reason": "llm_not_configured"}
        set_stage(job_id, "S3", "extract 0/7")

        def _prog(done: int, total: int, sec: str) -> None:
            set_stage(job_id, "S3", f"{sec} {done}/{total}")

        model = extract.extract_all(parsed, progress=_prog)
        mode = "llm"
        # Vision pass on image-only pages with critical findings: feed page images
        # to the vision model once and merge any rows it corrects.
        if parsed.image_only_pages and llm.get_config()["model_vision"]:
            set_stage(job_id, "S3", "vision")
            try:
                vision_rows = _vision_pass(parsed, model)
                _merge_rows(model, vision_rows)
            except Exception as e:  # vision is best-effort, never blocks the job
                audit(None, "vision_pass_warn", "contract", contract_no, {"error": str(e)[:300]})

    # S4: validate
    set_stage(job_id, "S4", "validate")
    expected = extract.parse_catalog()
    val = validate.validate(model, expected)
    if not val.ok:
        finish_job(job_id, contract_no, "failed", "validation_failed",
                   "; ".join(val.errors[:8]))
        return {"status": "failed", "errors": val.errors[:10]}
    if val.stats["rows"] != 82:
        finish_job(job_id, contract_no, "failed", "row_count",
                   f"got {val.stats['rows']} rows, expected 82")
        return {"status": "failed", "errors": [f"row_count={val.stats['rows']}"]}

    # Overall status reflects gate (any Action/Unclear -> follow-up required).
    if val.gate_review:
        model["cards"]["left"]["badge_enum"] = "FollowUp"
        model["cards"]["left"]["badge_label"] = "Follow-up required"
    else:
        model["cards"]["left"]["badge_enum"] = "Checked"
        model["cards"]["left"]["badge_label"] = "Checked"
    model["meta"]["assessment_date"] = time.strftime("%d %B %Y") + f"  |  {mode}  |  {WORKER_ID}"

    # Buyer-facing summary tables are derived by rule from the validated rows
    # above, so they can never contradict the checklist they summarise. Doing it
    # here (not in the prompt) keeps them out of the model's hands entirely.
    buyer_tables.derive(model)

    # S5: render PDF (English base; UI handles bilingual presentation)
    set_stage(job_id, "S5", "render")
    version = 1
    with server_conn() as conn:
        version = conn.execute(
            "SELECT coalesce(max(version),0)+1 FROM contract_reports WHERE contract_no=%s",
            (contract_no,)).fetchone()[0]
    out_dir = REPORTS_DIR / contract_no / f"v{version}"
    model_path = out_dir / "model.json"
    pdf_path = out_dir / "report.pdf"
    render.render(model, model_path, pdf_path, lang="en")
    pdf_sha = render.sha256(pdf_path)

    # S6 gate: record review requirement; report still produced (M3 will enforce blocking).
    set_stage(job_id, "S6", "gate")
    if val.gate_review:
        audit(None, "review_gate", "contract", contract_no,
              {"reasons": val.gate_reasons[:12], "version": version})

    # S7: ingest into A-G partitions + backfill scalars
    set_stage(job_id, "S7", "ingest")
    rel_pdf = to_relative(pdf_path)
    with server_conn() as conn:
        n = do_ingest(conn, model, contract_no,
                      report_path=rel_pdf, report_sha256=pdf_sha)
    finish_job(job_id, contract_no, "completed")
    return {"status": "completed", "mode": mode, "version": version,
            "items": n, "stats": val.stats, "pages": page_info,
            "gate_review": val.gate_review, "gate_reasons": val.gate_reasons[:12],
            "report_path": rel_pdf, "report_sha256": pdf_sha}


def _vision_pass(parsed: parse.ParsedDoc, model: dict) -> list[dict]:
    """Ask the vision model to review image-only pages for critical rows."""
    focus = [r for s in model["sections"] for r in s["rows"]
             if r["code"] in ("A6", "A7", "E1", "G2")]
    prompt = (
        "These are image-only pages from a Section 32 contract pack. For each "
        "checklist row below, report evidence with the visible page number. "
        "Return JSON {\"rows\":[{\"code\",\"contents\":[...],\"verified\"}]}. "
        "Never guess a page or certificate number.\n"
        + json.dumps(focus, ensure_ascii=False)
    )
    images = [p.png_b64 for p in parsed.image_only_pages if p.png_b64][:8]
    result = llm.extract_json_vision(prompt, images)
    return result.get("rows", []) if isinstance(result, dict) else []


def _merge_rows(model: dict, vision_rows: list[dict]) -> None:
    by_code = {r.get("code"): r for r in vision_rows}
    for sec in model["sections"]:
        for row in sec["rows"]:
            v = by_code.get(row["code"])
            if not v:
                continue
            extra = [str(x) for x in v.get("contents", [])]
            if extra:
                row["contents"] = list(dict.fromkeys(row["contents"] + extra))
            if v.get("verified") in ("Action", "Unclear"):
                row["verified"] = v["verified"]


# ---------- entry ----------
def run_once(replay_model: dict | None = None, target_contract: str | None = None) -> dict | None:
    if db_status() != "up":
        raise RuntimeError("database is down")
    with server_conn() as conn:
        claimed = claim_job(conn, target_contract)
    if not claimed:
        return None
    job_id, contract_no, attempt = claimed
    # Freeze the admin-saved system config for this job. A save made while the
    # analysis is running applies to the NEXT job, never halfway through a report.
    hb_state("busy", job_id, contract_no)
    heartbeat("busy", job_id, contract_no, "S0")
    llm.begin_job_config()
    try:
        return process(job_id, contract_no, attempt, replay_model)
    except Exception as e:
        finish_job(job_id, contract_no, "failed", "worker_exception",
                   f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return {"status": "failed", "error": str(e)}
    finally:
        llm.end_job_config()
        hb_state("idle")
        heartbeat("idle")


def main() -> None:
    ap = argparse.ArgumentParser(description="Contract review M2 worker")
    ap.add_argument("--once", action="store_true", help="process at most one job then exit")
    ap.add_argument("--replay-model", help="offline: use this validated model JSON instead of LLM")
    ap.add_argument("--contract-no", help="only process this contract (others are requeued)")
    a = ap.parse_args()

    replay = None
    if a.replay_model:
        replay = json.loads(Path(a.replay_model).read_text(encoding="utf-8"))
    print(f"[{WORKER_ID}] started | LLM={llm.config_summary()} | replay={bool(replay)}", flush=True)

    heartbeat("idle")
    start_heartbeat_thread()
    try:
        if a.once:
            result = run_once(replay, a.contract_no)
            # Single-line JSON so callers/tests can parse stdout easily.
            print(json.dumps(result or {"status": "no_queued_job"}, ensure_ascii=False), flush=True)
            return

        while True:
            try:
                result = run_once(replay, a.contract_no)
                if result and result.get("status") not in (None, "no_queued_job"):
                    print(json.dumps(result, ensure_ascii=False), flush=True)
            except Exception as e:
                print(f"[{WORKER_ID}] loop error: {e}", file=sys.stderr, flush=True)
            # run_once() returns early when there is nothing to claim, so the
            # idle beat has to live here too: it is the only proof of life the
            # UI has while the queue is empty.
            heartbeat("idle")
            time.sleep(POLL)
    except KeyboardInterrupt:
        print(f"[{WORKER_ID}] stopped by user", flush=True)
    finally:
        hb_state("stopped")
        heartbeat("stopped")


if __name__ == "__main__":
    main()
