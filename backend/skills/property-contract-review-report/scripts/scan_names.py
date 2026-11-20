#!/usr/bin/env python3
"""Scan a contract pack for every form of a party's name, with page numbers.

Why this exists: A1 is the row most often judged wrong, and the reason is always
the same — the reviewer compares the two full names it happened to read (contract
particulars vs title search), finds them identical, and marks "Identity spelling:
MATCH". The abbreviated forms live on the rates notice and the SRO certificates,
where nobody looked.

This script does the looking. It pulls the registered proprietor's name off the
title search, takes the surname, then scans *every page* for lines containing it.

    python scan_names.py --pdf pack.pdf
    python scan_names.py --pdf pack.pdf --name "TUAN DINH TRINH" --json

Output is evidence for A1/A3/G26 — the script tells you which forms exist and
where. It does not decide the verdict for you: if it cannot classify the
surrounding context it says so rather than defaulting to MATCH.

Classification uses a 3-line window, because certificates print the label and the
value on separate lines:

    Vendor
    TUAN TRINH

"Your Reference", "Client Reference", "LD:..." and "ref ..." are search
reference codes, never name fields. A cover-page "ref Trinh" is not a vendor name.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

try:
    import pymupdf
except ImportError:  # pragma: no cover
    print("PyMuPDF required: pip install pymupdf", file=sys.stderr)
    sys.exit(2)

# A real name field names the person.
NAME_FIELD_RE = re.compile(
    r"\b(vendor'?s?\s+name|vendor|registered\s+proprietor|proprietor|purchaser|"
    r"owner|ratepayer|applicant|customer|land\s+owner|taxpayer)\b", re.I)
# Words that point at a reference code rather than a person.
REFERENCE_RE = re.compile(
    r"(your\s+reference|client\s+reference|reference\s*:|ref\s*[:#]|LD:|"
    r"envelope\s+id|information\s+statement\s+number|conveyancing\s+account)", re.I)

STOP = {"ESTATE", "FEE", "SIMPLE", "SOLE", "PROPRIETOR", "REGISTERED", "VENDOR",
        "MORTGAGE", "PURCHASER", "OF", "THE", "AND", "JOINT", "TENANTS", "AS",
        "MR", "MRS", "MS", "DR"}


def find_proprietor(doc) -> list[str]:
    """Registered proprietor names, taken from the title search page.

    Matched strictly: "you become the registered proprietor" appears in the
    contract boilerplate on early pages and would otherwise win the search.
    """
    names: list[str] = []
    pat = re.compile(r"REGISTERED\s+PROPRIETOR(.*?)(?:ENCUMBRANCES|$)", re.I | re.S)
    for page in doc:
        text = page.get_text("text") or ""
        m = pat.search(text)
        if not m:
            continue
        for line in m.group(1).splitlines()[1:8]:
            cand = re.split(r"\s+of\s+", line.strip())[0].strip().rstrip(",")
            if not re.fullmatch(r"[A-Z][A-Z'\-. ]{4,60}", cand):
                continue
            toks = cand.split()
            if not 2 <= len(toks) <= 4 or any(t in STOP for t in toks):
                continue
            names.append(cand)
        if names:
            break
    return list(dict.fromkeys(names))


def extract_form(line: str, surname: str) -> str:
    """The name-shaped run in this line, as printed (uppercased, spaces collapsed)."""
    m = re.search(rf"([A-Z][A-Z'\.\- ]{{0,44}}?\b{re.escape(surname)}\b)", line, re.I)
    raw = m.group(1) if m else surname
    return re.sub(r"\s+", " ", raw.upper()).strip(" ,.")
    

def classify(window: str) -> str:
    """name-field / reference / other, judged on the 3-line window."""
    if REFERENCE_RE.search(window) and not re.search(
            r"\b(vendor'?s?\s+name|registered\s+proprietor)\b", window, re.I):
        return "reference"
    if NAME_FIELD_RE.search(window):
        return "name-field"
    return "other"


def scan(doc, surname: str) -> list[dict]:
    hits: list[dict] = []
    if not surname:
        return hits
    word = re.compile(rf"\b{re.escape(surname)}\b", re.I)
    for pno, page in enumerate(doc, 1):
        lines = [re.sub(r"\s+", " ", l).strip()
                 for l in (page.get_text("text") or "").splitlines()]
        lines = [l for l in lines if l]
        for i, line in enumerate(lines):
            if not word.search(line):
                continue
            window = " | ".join(lines[max(0, i - 1):i + 2])
            hits.append({
                "page": pno,
                "form": extract_form(line, surname),
                "kind": classify(window),
                "line": line[:200],
                "context": window[:220],
            })
    return hits


def summarise(hits: list[dict]) -> dict:
    by_form: dict[str, list[int]] = defaultdict(list)
    kinds: dict[str, set] = defaultdict(set)
    for h in hits:
        by_form[h["form"]].append(h["page"])
        kinds[h["form"]].add(h["kind"])

    forms = sorted(
        ({"form": f, "pages": sorted(set(by_form[f])), "kinds": sorted(kinds[f])}
         for f in by_form),
        key=lambda d: (-len(d["pages"]), d["form"]))

    name_forms = [f for f in forms if "name-field" in f["kinds"]]
    # A form seen only in unidentified context still counts if it is a different
    # spelling of the name — undercounting is what produced the wrong MATCH.
    other_forms = [f for f in forms if f["kinds"] == ["other"]]
    if len(name_forms) > 1:
        verdict = "MISMATCH"
        basis = f"{len(name_forms)} distinct forms appear in name fields"
    elif len(name_forms) == 1 and not other_forms:
        verdict = "MATCH"
        basis = "one form only, seen in name fields"
    elif len(name_forms) == 1:
        verdict = "REVIEW"
        basis = (f"1 form in name fields, but {len(other_forms)} other form(s) in "
                 f"unclassified context — check them before marking MATCH")
    elif other_forms:
        verdict = "REVIEW"
        basis = ("no name field could be classified (label may be on another page); "
                 "review the hits below by hand")
    else:
        verdict = "MATCH"
        basis = "only the full name was found in the pack"

    return {
        "verdict_hint": verdict,
        "basis": basis,
        "forms": forms,
        "name_field_forms": [f["form"] for f in name_forms],
        "reference_only": sorted({h["form"] for h in hits if h["kind"] == "reference"}),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Scan a pack for name variants")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--name", action="append", default=[],
                    help="full name(s) to look for; repeatable. Omit to read the "
                         "registered proprietor from the title search.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-lines", type=int, default=40)
    a = ap.parse_args()

    path = Path(a.pdf)
    if not path.exists():
        print(f"pdf not found: {path}", file=sys.stderr)
        return 2
    doc = pymupdf.open(path)

    names = [n.upper() for n in a.name] or find_proprietor(doc)
    if not names:
        print("could not locate a registered proprietor; pass --name", file=sys.stderr)
        return 1

    out = []
    for full in names:
        toks = [t for t in re.split(r"[\s,]+", full.strip()) if t and t not in STOP]
        surname = toks[-1] if toks else ""
        hits = scan(doc, surname)
        out.append({"full_name": full, **summarise(hits), "hits": hits})

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    for item in out:
        print(f"full name: {item['full_name']}")
        print(f"hint: Identity spelling: {item['verdict_hint']}  ({item['basis']})")
        print()
        print(f"{'form':30s} {'pages':24s} where")
        for f in item["forms"]:
            pages = ", ".join(str(p) for p in f["pages"][:8])
            print(f"{f['form'][:29]:30s} {pages:24s} {'/'.join(f['kinds'])}")
        if item["reference_only"]:
            print(f"\nreference codes only (NOT name fields): "
                  f"{', '.join(item['reference_only'])}")
        print("\nline hits:")
        for h in item["hits"][:a.max_lines]:
            tag = {"name-field": "NAME", "reference": "REF ", "other": "?   "}[h["kind"]]
            print(f"  p{h['page']:<3d} [{tag}] {h['line'][:110]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
