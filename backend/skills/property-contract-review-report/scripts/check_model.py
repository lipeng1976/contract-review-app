#!/usr/bin/env python3
"""Pre-flight check for a Section 32 review model JSON.

Run this BEFORE build_review_report.py. It catches the failure modes that are
invisible to a human skimming a finished PDF: a verdict that contradicts its own
evidence, a row marked Checked while its contents says "not located", an invented
enum value, a row list that drifted from the catalog, or an empty buyer-matters
table.

    python check_model.py --model model.json [--lang en] [--strict] [--json]

Exit code 0 = clean (or warnings only), 1 = at least one error.

Errors are things that must be fixed before rendering. Warnings are judgement
calls worth a second look (e.g. a row whose contents carries no page reference).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
CATALOG = SKILL_DIR / "references" / "checklist-catalog.md"

PROVIDED_ENUM = {"Present", "Missing", "Unclear", "Awaiting", "NA"}
VERIFIED_ENUM = {"Checked", "Action", "Unclear", "NA"}
BADGE_ENUM = {"Present", "Missing", "Unclear", "Awaiting", "NA",
              "Checked", "Action", "FollowUp"}

# Verdicts the wording rules allow as the emphasised value of a contents line.
VERDICT_WORDS = {"MATCH", "MISMATCH", "YES", "NO", "NONE", "N/A", "NA",
                 "APPLY", "NIL"}

# Invented status words seen in real runs. "CHECKED" / "CONFLICT" are the two
# that slipped through before: they look like verdicts but are not in the enum,
# so downstream aggregators (G30, the badge, the buyer tables) cannot read them.
INVENTED_STATUS = re.compile(
    r"\b(CONFLICT|CHECKED|UNCONFIRMED|UNKNOWN|PARTIAL|PENDING|TBC|TBD|UNSURE)\b")

# Phrases that contradict Verified=Checked.
CONTRADICTS_CHECKED = [
    "not located", "not in pack", "cited but not", "no copy",
    "unconfirmed", "unable to provide", "not provided", "missing",
    "not stated", "not found",
]

# A Checked row may describe the document, not ask for work. An imperative verb
# in a Checked row means the row is really an Action ("…; confirm any release at
# settlement"). Word boundaries matter: "obtained" / "verified" are past
# participles and must not fire.
ACTION_VERB_RE = re.compile(
    r"\b(confirm|obtain|verify|arrange|reconcile|ensure|request|instruct|"
    r"order updated|have reviewed|seek advice)\b", re.I)

# Estate / executor sales: identifying the relationship is not verifying the
# authority to sell. Red line — these rows may not be Checked.
ESTATE_RE = re.compile(
    r"\b(executor|executrix|administrator|probate|letters of administration|"
    r"grant of representation|estate of|deceased)\b", re.I)
ESTATE_HOME_ROWS = ("A1", "A7", "G26")

# S32 form answers that are *silence*, not an express "not applicable".
BLANK_FORM_RE = re.compile(
    r"(not completed|left blank|not filled|not marked|no entry|box unmarked|"
    r"section \d+(?:\.\d+)? (?:was |is )?(?:not completed|blank))", re.I)
EXPLICIT_NA_RE = re.compile(r"N/A\s*[-–]\s*explicit", re.I)

# "Page 1 of 3" inside an authority document means the copy is incomplete.
PAGE_OF_RE = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)", re.I)

# Volume/Folio written as "8118 / 799", "Vol 8118 Fol 799" or "Volume 8118, Folio 799".
TITLE_REF_A = re.compile(r"\b(\d{3,5})\s*/\s*(\d{1,4})\b")
TITLE_REF_B = re.compile(r"Vol(?:ume)?\.?\s*(\d{3,5})\s*,?\s*Fol(?:io)?\.?\s*(\d{1,4})", re.I)


def title_refs(text: str) -> set[str]:
    refs = {f"{a}/{b}" for a, b in TITLE_REF_A.findall(text)}
    refs |= {f"{a}/{b}" for a, b in TITLE_REF_B.findall(text)}
    return refs

# Facts that appear in several rows; Provided must be the same everywhere.
# Grouped by the *same item*, not by the same topic: "is there an owners
# corporation" (C6, D9) and "are its certificates attached" (D10-D14, G16-G21)
# are different questions and may legitimately differ.
FACT_GROUPS = {
    "owners corporation - applicability": ("C6", "D9"),
    "owners corporation - documents": ("D10", "D11", "D12", "D13", "D14",
                                       "G16", "G17", "G18", "G19", "G20", "G21"),
    "owner-builder - report": ("D3", "G22"),
    "owner-builder - insurance": ("D4", "G23"),
    "GAIC": ("C7", "C8", "C9", "G13"),
    "subdivision": ("A8", "A9", "A10", "G27"),
}

CROSSREF_ONLY_RE = re.compile(r"^(see|refer to|as per)\b", re.I)
PRODUCED_RE = re.compile(r"Produced\s+(\d{2})/(\d{2})/(\d{4})", re.I)
# "current" is a substring of "currently encumbering", so these must be phrases,
# not bare words — otherwise every real A4 suppresses the staleness warning.
STALE_RE = re.compile(
    r"(older than|stale|refreshed?\b|point[- ]in[- ]time|no later dealing|"
    r"out of date|re-?order)", re.I)
# ...unless the line is explicitly describing the *other* document in a
# comparison (e.g. "Asset plan: Cited but not located" inside an Action row).
# Those rows are not Checked, so the check below never fires on them.

PAGE_RE = re.compile(r"\(\s*p{1,2}\.\s*\d+(?:\s*[-,–]\s*(?:p{1,2}\.\s*)?\d+)*\s*\)")
LINE_LABEL_RE = re.compile(r"^[-–•\s]*(?:<b>)?[^:<]{1,60}(?:</b>)?\s*:")
MATCH_RE = re.compile(r"\b(MATCH|MISMATCH)\b")

# Rows whose whole point is a verdict about agreement or existence. Rows that
# report a value (A2 title refs, A3 identity, B3/B4 scheme and zone) are excluded
# on purpose: demanding "MATCH/YES" from them produces noise, not signal.
VERDICT_CODES = {"A1", "A4", "A5", "B1", "B2", "B5", "B7"}
A1_REQUIRED = ("property consistency", "identity spelling")


def catalog_codes() -> list[str]:
    """The canonical 82 codes, in catalog order."""
    if not CATALOG.exists():
        return []
    codes = []
    for line in CATALOG.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*([A-G]\d{1,2})\s*\|", line)
        if m:
            codes.append(m.group(1))
    return codes


def flat_text(row: dict) -> str:
    c = row.get("contents") or []
    if isinstance(c, str):
        c = [c]
    return " ".join(str(x) for x in c)


def _date_or_none(s: str):
    from datetime import date
    try:
        y, m, d = (int(x) for x in s.split("-"))
        return date(y, m, d)
    except Exception:
        return None


def _model_date(model: dict):
    """Best-effort assessment date from meta (ISO, 'D Month YYYY', or 中文)."""
    from datetime import date
    blob = " ".join(str(v) for v in (model.get("meta") or {}).values())
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", blob)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{1,2})\s+([A-Z][a-z]+)\s+(20\d{2})", blob)
    if m:
        months = ["january", "february", "march", "april", "may", "june", "july",
                  "august", "september", "october", "november", "december"]
        try:
            return date(int(m.group(3)), months.index(m.group(2).lower()) + 1,
                        int(m.group(1)))
        except ValueError:
            return None
    m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", blob)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.stats: dict = {}

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def check(model: dict, lang: str = "en") -> Report:
    r = Report()
    expected = catalog_codes()

    sections = model.get("sections") or []
    if not sections:
        r.err("model has no sections")
        return r

    rows: list[dict] = []
    for sect in sections:
        for row in sect.get("rows") or []:
            rows.append(row)

    # ── 1. row set vs catalog ────────────────────────────────────────────────
    got = [row.get("code") for row in rows]
    if expected:
        missing = [c for c in expected if c not in got]
        extra = [c for c in got if c not in expected]
        dupes = sorted({c for c in got if got.count(c) > 1})
        if missing:
            r.err(f"missing rows: {', '.join(missing)}")
        if extra:
            r.err(f"rows not in catalog: {', '.join(extra)}")
        if dupes:
            r.err(f"duplicate rows: {', '.join(dupes)}")
        order_ok = [c for c in got if c in expected] == [c for c in expected if c in got]
        if not order_ok:
            r.warn("rows are not in catalog order")
    else:
        r.warn(f"catalog not readable at {CATALOG}; row-set check skipped")

    # ── 2. enums ─────────────────────────────────────────────────────────────
    for row in rows:
        code = row.get("code", "?")
        p, v = row.get("provided"), row.get("verified")
        if p not in PROVIDED_ENUM:
            r.err(f"{code}: provided={p!r} not in {sorted(PROVIDED_ENUM)}")
        if v not in VERIFIED_ENUM:
            r.err(f"{code}: verified={v!r} not in {sorted(VERIFIED_ENUM)}")
        # N/A + Checked is not a judgement, it is a leftover: nothing to provide
        # cannot also have been verified against a document.
        if p == "NA" and v in ("Checked", "Action"):
            r.err(f"{code}: provided=NA but verified={v} — nothing to provide "
                  f"cannot be {v}; either the item applies (Present/…) or use NA/NA")
    badge = (((model.get("cards") or {}).get("left") or {}).get("badge_enum"))
    if badge and badge not in BADGE_ENUM:
        r.err(f"cards.left.badge_enum={badge!r} not in {sorted(BADGE_ENUM)}")

    # ── 3. contents shape ────────────────────────────────────────────────────
    no_page, no_label, empty = [], [], []
    for row in rows:
        code = row.get("code", "?")
        contents = row.get("contents") or []
        if isinstance(contents, str):
            contents = [contents]
        if not contents or not any(str(x).strip() for x in contents):
            empty.append(code)
            continue
        for line in contents:
            s = str(line).strip()
            if not s:
                continue
            if not LINE_LABEL_RE.match(s):
                no_label.append(f"{code}: {s[:50]!r}")
            if not PAGE_RE.search(s) and row.get("verified") not in ("NA",):
                no_page.append(f"{code}: {s[:50]!r}")
    if empty:
        r.err(f"empty contents: {', '.join(empty)}")
    for item in no_label[:8]:
        r.warn(f"contents line without a 'label: value' lead — {item}")
    for item in no_page[:10]:
        r.warn(f"contents line without a page reference — {item}")

    # ── 4. invented status words ─────────────────────────────────────────────
    for row in rows:
        m = INVENTED_STATUS.search(flat_text(row))
        if m:
            r.err(f"{row.get('code')}: invented status word {m.group(1)!r} "
                  f"(use the enum, not free-form capitals)")

    # ── 5. verdict rows must decide first ────────────────────────────────────
    by_code = {row.get("code"): row for row in rows}
    for code in VERDICT_CODES:
        row = by_code.get(code)
        if not row:
            continue
        text = flat_text(row)
        if not re.search(r"\b(MATCH|MISMATCH|YES|NO|NONE|APPLY|N/A)\b", text, re.I):
            r.warn(f"{code}: no verdict anywhere in contents — {text[:60]!r}")

    a1 = by_code.get("A1")
    if a1:
        joined = flat_text(a1).lower()
        for label in A1_REQUIRED:
            if label not in joined:
                r.err(f"A1 missing '{label}' verdict line")

    # ── 6. Checked vs its own evidence ───────────────────────────────────────
    for row in rows:
        if row.get("verified") != "Checked":
            continue
        low = flat_text(row).lower()
        hits = [p for p in CONTRADICTS_CHECKED if p in low]
        if hits:
            r.err(f"{row.get('code')}: Verified=Checked but contents says "
                  f"{hits[0]!r} — must be Action or Unclear")
        verb = ACTION_VERB_RE.search(flat_text(row))
        if verb:
            r.err(f"{row.get('code')}: Verified=Checked but contents is written as "
                  f"a task ({verb.group(1)!r}) — if the buyer must '{verb.group(1)}', "
                  f"the row is Action")

    # ── 7. MATCH with a contradiction inside the same row / across rows ──────
    for row in rows:
        text = flat_text(row)
        if re.search(r"\bMATCH\b", text) and not re.search(r"\bMISMATCH\b", text):
            # A row that claims MATCH while listing a variant of the name.
            variants = re.findall(r"^- <b>[^<]+</b>", text, re.M)
            if variants and row.get("code") in ("A1", "A3"):
                r.err(f"{row.get('code')}: claims MATCH but lists name variants "
                      f"({len(variants)}) — identity spelling is MISMATCH")

    if a1:
        a1_text = flat_text(a1)
        a1_match = bool(re.search(r"Identity spelling:\s*(<b>)?MATCH(</b>)?\b", a1_text))
        a1_mis = "MISMATCH" in a1_text
        variant_rows = [row.get("code") for row in rows
                        if re.search(r"\b(Name variant|Variant|Identity spelling: .*MISMATCH)",
                                     flat_text(row))]
        if a1_match and not a1_mis and variant_rows:
            r.err("A1 says 'Identity spelling: MATCH' but these rows list a name "
                  f"variant: {', '.join(variant_rows)} — A1 must be MISMATCH")
        if a1.get("verified") == "Checked" and (a1_mis or variant_rows):
            r.err("A1 is Verified=Checked while a spelling variant exists — "
                  "red line requires Action")

    # ── 8. s199 / grant misidentification ────────────────────────────────────
    g8 = by_code.get("G8")
    if g8 and g8.get("verified") == "Checked":
        low = flat_text(g8).lower()
        # Only the disclaimer itself counts. A genuine s199 certificate carries
        # "Purchaser: NOT KNOWN" and the word "not", so a bare "not" test fires
        # on every valid certificate.
        disclaimed = ("not a planning certificate" in low
                      or "not a certificate issued pursuant" in low
                      or "is not a planning certificate" in low
                      or "planning report is not" in low)
        if disclaimed:
            r.err("G8: Checked while the cited page says it is NOT a s199 "
                  "certificate — use Unclear/Unclear")
    for code in ("A7", "G26"):
        row = by_code.get(code)
        if not row:
            continue
        text = flat_text(row)
        if re.search(r"guarant", text, re.I) and not re.search(r"grant of (probate|representation)", text, re.I):
            r.err(f"{code}: cites a Guarantee/Guarantor document as the vendor's "
                  f"authority — 'grant' must match Grant of Probate/Representation")

    # ── 9. one fact, one home (Action spread) ────────────────────────────────
    name_action = [row.get("code") for row in rows
                   if row.get("verified") == "Action"
                   and re.search(r"name variant|identity spelling", flat_text(row), re.I)]
    if len(name_action) > 2:
        r.warn("name-variant Action repeated on "
               f"{len(name_action)} rows ({', '.join(name_action)}) — home rows are "
               "A1 + G26; others should only note 'Name variant: ... (p.N)'")

    # D3/D4 and G22/G23 describe the same owner-builder facts; split verdicts mean
    # one of the two rows was judged from the wrong source.
    for d, g in (("D3", "G22"), ("D4", "G23")):
        rd, rg = by_code.get(d), by_code.get(g)
        if rd and rg and (rd.get("verified") == "NA") != (rg.get("verified") == "NA"):
            r.err(f"{d}={rd.get('verified')} but {g}={rg.get('verified')} — same "
                  f"owner-builder fact, the two statuses must agree")

    # ── 9b. estate sales, incomplete authority, blank-form wording ───────────
    for code in ESTATE_HOME_ROWS:
        row = by_code.get(code)
        if not row:
            continue
        if ESTATE_RE.search(flat_text(row)) and row.get("verified") == "Checked":
            r.err(f"{code}: estate / executor sale marked Checked — red line: "
                  f"identifying the relationship is not verifying authority to sell; "
                  f"use Present/Action + 'Executor / estate relationship identified'")
    a3 = by_code.get("A3")
    if a3 and ESTATE_RE.search(flat_text(a3)) and a3.get("verified") == "Checked":
        # A3 naming an executor is fine — the authority verdict lives in A1/A7/G26.
        # Only flag it when A3 is the sole row claiming the authority was reviewed.
        others = [c for c in ("A1", "A7", "G26")
                  if by_code.get(c) and by_code[c].get("verified") == "Action"]
        if not others:
            r.err("A3 is the only row carrying the estate sale and it is Checked — "
                  "the authority verdict belongs to A1/A7/G26, which must be Action")

    for code in ("A7", "G26"):
        row = by_code.get(code)
        if not row:
            continue
        for first, total in PAGE_OF_RE.findall(flat_text(row)):
            if int(first) < int(total) and row.get("verified") != "Action":
                r.err(f"{code}: 'Page {first} of {total}' — the authority document is "
                      f"an incomplete copy, must be Present/Action")

    # Calling a blank form box "explicit" is a wording lie: the form said nothing.
    for row in rows:
        text = flat_text(row)
        if EXPLICIT_NA_RE.search(text) and BLANK_FORM_RE.search(text):
            r.err(f"{row.get('code')}: 'N/A - explicit' next to "
                  f"{BLANK_FORM_RE.search(text).group(1)!r} — a blank box is "
                  f"'N/A - box unmarked (p.N)' with Provided=Present")

    # A cross-reference is a pointer, not a finding.
    for row in rows:
        c = row.get("contents") or []
        c = [str(x).strip() for x in (c if isinstance(c, list) else [c]) if str(x).strip()]
        if len(c) == 1 and CROSSREF_ONLY_RE.match(c[0]):
            r.warn(f"{row.get('code')}: only a cross-reference ({c[0][:40]!r}) — "
                   f"add the figure or a one-line finding")

    # ── 9c. one fact, one Provided ───────────────────────────────────────────
    for fact, codes in FACT_GROUPS.items():
        got_p = {c: by_code[c].get("provided") for c in codes if c in by_code}
        if len(set(got_p.values())) > 1:
            spread = ", ".join(f"{c}={v}" for c, v in got_p.items())
            r.warn(f"{fact}: same fact, different Provided ({spread}) — the S32 "
                   f"answer is one statement and maps to one Provided")

    # A row cannot say "single parcel, all certificates match" while naming a
    # title reference that is not the subject title (SRO certificates do this).
    g29 = by_code.get("G29")
    if g29:
        g29_text = flat_text(g29)
        subject = title_refs(flat_text(by_code.get("A2") or {}))
        foreign = title_refs(g29_text) - subject if subject else set()
        claims_single = re.search(r"single parcel|single title|same parcel|all match",
                                  g29_text, re.I)
        if claims_single and foreign:
            r.err("G29 claims a single / matching parcel but cites "
                  f"{', '.join(sorted(foreign))}, which is not the subject title "
                  f"({', '.join(sorted(subject)) or 'see A2'}) — say which land it is")

    # ── 9d. stale title search vs assessment date ────────────────────────────
    produced = PRODUCED_RE.search(" ".join(flat_text(by_code.get(c) or {})
                                           for c in ("A2", "A4", "G1")))
    if produced:
        d, mth, y = produced.groups()
        t = _date_or_none(f"{y}-{mth}-{d}")
        a = _model_date(model)
        if t and a and (a - t).days > 120:
            ctx = " ".join(flat_text(by_code.get(c) or {}) for c in ("A2", "A4", "G1"))
            if not STALE_RE.search(ctx):
                r.warn(f"title search produced {t} is {(a - t).days} days before the "
                       f"assessment date and no row flags it — say 'search produced "
                       f"{t}; confirm no later dealing' in A4 or G1")

    # ── 10. buyer tables must not be empty ───────────────────────────────────
    fu = (model.get("followup") or {}).get("rows") or []
    ot = (model.get("other") or {}).get("rows") or []
    actions = [row.get("code") for row in rows if row.get("verified") == "Action"]
    unclears = [row.get("code") for row in rows if row.get("verified") == "Unclear"]
    if not fu:
        r.err(f"followup.rows is empty although {len(actions)} rows are Action — "
              "the buyer-matters table is the point of the report")
    if not ot and len(actions) + len(unclears) > 0:
        r.warn("other.rows is empty — expected 3-6 rows on controls / outgoings / occupancy")
    for table, name in ((fu, "followup"), (ot, "other")):
        for i, row in enumerate(table, 1):
            if not str(row.get("matter") or "").strip():
                r.err(f"{name} row {i}: empty matter")
            if not (row.get("finding") or []):
                r.err(f"{name} row {i}: empty key finding")
    if not (model.get("bullets") or []):
        r.warn("bullets is empty — expected the evidence/legal-notes block")

    # ── 11. meta ─────────────────────────────────────────────────────────────
    meta = model.get("meta") or {}
    if not str(meta.get("address") or "").strip():
        r.warn("meta.address is empty")
    if not (model.get("note_box") or {}).get("body"):
        r.warn("note_box.body is empty — the disclaimer is mandatory")

    r.stats = {
        "rows": len(rows),
        "expected_rows": len(expected) or None,
        "action": len(actions),
        "unclear": len(unclears),
        "checked": sum(1 for row in rows if row.get("verified") == "Checked"),
        "followup_rows": len(fu),
        "other_rows": len(ot),
        "errors": len(r.errors),
        "warnings": len(r.warnings),
    }
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate a Section 32 review model JSON")
    ap.add_argument("--model", required=True)
    ap.add_argument("--lang", default="en")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()

    path = Path(a.model)
    if not path.exists():
        print(f"model not found: {path}", file=sys.stderr)
        return 2
    model = json.loads(path.read_text(encoding="utf-8"))
    rep = check(model, a.lang)

    if a.json:
        print(json.dumps({"ok": not rep.errors and not (a.strict and rep.warnings),
                          "stats": rep.stats,
                          "errors": rep.errors, "warnings": rep.warnings},
                         ensure_ascii=False, indent=2))
    else:
        print(f"model: {path}")
        print("stats: " + ", ".join(f"{k}={v}" for k, v in rep.stats.items()))
        if rep.errors:
            print(f"\nERRORS ({len(rep.errors)})")
            for e in rep.errors:
                print("  ✗", e)
        if rep.warnings:
            print(f"\nWARNINGS ({len(rep.warnings)})")
            for w in rep.warnings:
                print("  ! ", w)
        if not rep.errors and not rep.warnings:
            print("\nclean.")
        elif not rep.errors:
            print("\nno errors.")

    if rep.errors:
        return 1
    if a.strict and rep.warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
