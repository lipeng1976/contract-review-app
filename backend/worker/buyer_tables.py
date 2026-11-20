"""Derive the two buyer-facing summary tables from the checklist rows.

The checklist is 82 rows of evidence; a buyer cannot read 82 rows. The report's
value sits in the two tables at the end — "what needs your lawyer to act" and
"what you should know before you bid" — and they were being left empty because
they were nobody's job: the model fills `sections`, the renderer prints whatever
it is given.

This module fills them by rule, not by another model call. Every line is traceable
back to rows that were already validated, so the summary cannot invent a finding.

Two rules matter:
  * group by topic, not by code — 20 Action rows must not become 20 table lines;
  * a topic only appears when at least one of its rows is actually Action/Unclear
    (for follow-up) or has real content (for the information table).
"""
from __future__ import annotations

import re

PAGE_PAT = re.compile(r"\(p{1,2}\.\s*(\d+)(?:\s*[-–]\s*(\d+))?\)")
MAX_PAGES_IN_SPAN = 12      # guard against a stray "(pp.1-72)" swallowing the table


def _plain(s: str) -> str:
    return re.sub(r"</?b>", "", str(s)).strip()


def _pages(lines: list[str]) -> str:
    nums: set[int] = set()
    for line in lines:
        for a, b in PAGE_PAT.findall(_plain(line)):
            start = int(a)
            end = int(b) if b else start
            if end - start > MAX_PAGES_IN_SPAN:
                end = start + MAX_PAGES_IN_SPAN
            nums.update(range(start, end + 1))
    return ", ".join(str(n) for n in sorted(nums))


FOLLOWUP_TOPICS = [
    {
        "matter": "Vendor identity and authority",
        "codes": ["A1", "A3", "A7", "G26"],
        "action": [
            "Obtain written confirmation that the abbreviated entries on the rates notice "
            "and SRO certificates are the same registered proprietor",
            "Obtain the complete grant of representation or authority document before signing",
        ],
    },
    {
        "matter": "Works carried out without permits",
        "codes": ["D2", "D3", "D4", "G22", "G23"],
        "action": [
            "Request the building permit, occupancy certificate and domestic building "
            "insurance for the disclosed works, or written confirmation that none exists",
            "Ask a building surveyor whether the works require a permit and what "
            "regularisation would cost",
        ],
    },
    {
        "matter": "Planning certificate",
        "codes": ["B3", "G8"],
        "action": [
            "Obtain the s199 Planning Certificate from the responsible council; the "
            "planning report in the pack is not a certificate",
        ],
    },
    {
        "matter": "Tenancy and vacant possession",
        "codes": ["E1", "E3", "G24"],
        "action": [
            "Confirm that any tenancy ends before settlement and that vacant possession "
            "will be given on the day",
        ],
    },
    {
        "matter": "Water statement and asset plan",
        "codes": ["G4", "G5"],
        "action": [
            "Obtain the missing water statement / asset plan pages, or confirm the "
            "sewer and drainage positions on site",
        ],
    },
    {
        "matter": "Outgoings at settlement",
        "codes": ["C10", "C11", "C12", "C13"],
        "action": [
            "Obtain updated rates, water and land tax figures for the settlement "
            "adjustment; the figures in the pack are dated",
        ],
    },
    {
        "matter": "EPA certificate applicability",
        "codes": ["G14"],
        "action": [
            "Confirm with the council whether an EPA certificate is required for the "
            "property's current or proposed use",
        ],
    },
]

OTHER_TOPICS = [
    {
        "matter": "Planning controls",
        "codes": ["B3", "B4", "B5", "B8"],
        "consider": [
            "Check how the zone, overlays and heritage controls affect any extension, "
            "redevelopment or subdivision",
        ],
    },
    {
        "matter": "Tree controls",
        "codes": ["B6"],
        "consider": [
            "A planning permit may be needed before removing, destroying or lopping "
            "protected trees",
        ],
    },
    {
        "matter": "Building records",
        "codes": ["D5", "D6", "D8"],
        "consider": [
            "A 'none issued' result covers only the period stated in the certificate, "
            "not the life of the buildings",
        ],
    },
    {
        "matter": "Service connections",
        "codes": ["D7"],
        "consider": [
            "Confirm connection charges and any outstanding amounts with the suppliers "
            "before settlement",
        ],
    },
    {
        "matter": "Current outgoings",
        "codes": ["C10", "C11"],
        "consider": [
            "These are dated current balances, not assessed arrears; adjust at settlement",
        ],
    },
]


def _rows_by_code(model: dict) -> dict[str, dict]:
    out = {}
    for sect in model.get("sections") or []:
        for row in sect.get("rows") or []:
            if row.get("code"):
                out[row["code"]] = row
    return out


def _lines(row: dict, limit: int = 3) -> list[str]:
    contents = row.get("contents") or []
    if isinstance(contents, str):
        contents = [contents]
    out = [_plain(c) for c in contents if str(c).strip()]
    return out[:limit]


def _dedupe(lines: list[str]) -> list[str]:
    """Drop repeats that differ only in page references.

    A1 and G26 both say "Identity spelling: MISMATCH" with different pages; the
    buyer should read that once.
    """
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = re.sub(r"\(p{1,2}\.[^)]*\)", "", line).strip().lower()
        key = re.sub(r"\s+", " ", key)
        if key and key not in seen:
            seen.add(key)
            out.append(line)
    return out


def derive(model: dict) -> dict:
    """Mutate `model` in place, adding followup / other / bullets."""
    rows = _rows_by_code(model)
    if not rows:
        return model

    followup, other = [], []

    for topic in FOLLOWUP_TOPICS:
        picked = [rows[c] for c in topic["codes"]
                  if c in rows and rows[c].get("verified") in ("Action", "Unclear")]
        if not picked:
            continue
        finding: list[str] = []
        raw: list[str] = []
        for row in picked:
            lines = _lines(row)
            raw.extend(lines)
            finding.extend(lines[:2])
        finding = _dedupe(finding)
        followup.append({
            "matter": topic["matter"],
            "finding": finding[:5],
            "action": list(topic["action"]),
            "pages": _pages(raw) or "—",
        })

    for topic in OTHER_TOPICS:
        picked = [rows[c] for c in topic["codes"] if c in rows]
        picked = [r for r in picked if _lines(r)]
        if not picked:
            continue
        finding, raw = [], []
        for row in picked:
            lines = _lines(row)
            raw.extend(lines)
            finding.extend(lines[:2])
        finding = _dedupe(finding)
        other.append({
            "matter": topic["matter"],
            "finding": finding[:5],
            "action": list(topic["consider"]),
            "pages": _pages(raw) or "—",
        })

    if followup:
        model.setdefault("followup", {})
        model["followup"].update({
            "title": model["followup"].get("title") or "Buyer key matters",
            "subheading": "Matters requiring follow-up",
            "columns": ["Matter", "Key finding", "Next step", "PDF pages"],
            "rows": followup,
        })
    if other:
        model.setdefault("other", {})
        model["other"].update({
            "subheading": "Other important property information",
            "columns": ["Matter", "Key finding", "Buyer to consider", "PDF pages"],
            "rows": other,
        })

    bullets = [
        "Checklist definitions and statutory notes are taken from the Section 32 "
        "checklist catalog.",
        "Page numbers are 1-based PDF pages of the supplied package.",
        "Findings are document-review results only and are not legal compliance "
        "conclusions.",
    ]
    authority = [rows[c] for c in ("A7", "G26") if c in rows]
    if any(r.get("verified") != "Checked" for r in authority):
        bullets.append("Legal ownership and sale authority have not been verified.")
    model["bullets_heading"] = model.get("bullets_heading") or "Evidence and legal notes"
    model["bullets"] = bullets
    return model


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    src = Path(sys.argv[1])
    m = json.loads(src.read_text(encoding="utf-8"))
    derive(m)
    out = src.with_name(src.stem + ".with_tables.json")
    out.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"followup rows: {len(m['followup']['rows'])}, "
          f"other rows: {len(m['other']['rows'])}, bullets: {len(m['bullets'])}")
    print("wrote", out)
