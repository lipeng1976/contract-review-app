#!/usr/bin/env python3
"""Scan a contract bundle for the clauses that decide the hard rows.

The three things that get missed in practice are not subtle read errors, they
are things the text layer hides:

1. **Image-only pages.** Plans and imaged folios carry no text, so an easement
   label ("E-1"), a creating instrument number or a carriageway notation simply
   is not there to be read. Those pages must be looked at as images.
2. **Incomplete authority documents.** A grant of probate supplied as "Page 1 of
   3" reads perfectly well as text and is still an incomplete copy.
3. **Special conditions that contradict the Section 32 form.** The form says
   "Owner-Builder: Not Applicable" while special condition 5.7 has the purchaser
   acknowledging an owner-builder inspection report. Only a keyword sweep of the
   special conditions finds that.

    python scan_clauses.py --pdf pack.pdf [--json] [--min-chars 50]

Exit code 0 always; this is an evidence dump, not a validator.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict

try:
    import pymupdf
except ImportError:  # pragma: no cover
    try:
        import fitz as pymupdf  # type: ignore
    except ImportError:
        print("PyMuPDF required: pip install pymupdf", file=sys.stderr)
        sys.exit(2)

# theme -> (regex, which rows it feeds)
THEMES: dict[str, tuple[str, str]] = {
    "unpermitted works": (
        r"(without (a )?(relevant )?permit|permits? \(statutory|whether or not all "
        r"relevant permits|building permit[s]? (have|has) (not )?been obtained|"
        r"owner[- ]builder|137B|unpermitted|unauthori[sz]ed works)",
        "D2 D3 D4 G22 G23",
    ),
    "asbestos / contamination": (
        r"(asbestos|contaminat)", "G14 + buyer table",
    ),
    "notices / orders": (
        r"(building notice|building order|notice to comply|fire hazard|"
        r"outstanding building notice)", "F1 G12",
    ),
    "mortgage / encumbrance": (
        r"(mortgage|encumbrance|caveat)", "A4 C4 G2",
    ),
    "lease / occupancy": (
        r"(\blease\b|\btenan|\bvacant possession|\blicence\b)", "E1 E3 G24",
    ),
    "easement / covenant": (
        r"(easement|covenant|carriageway|right of way|right-of-way)", "A6 A11 G2",
    ),
    "subdivision / GAIC": (
        r"(unregistered subdivision|staged subdivision|further plan|GAIC|"
        r"work[- ]in[- ]kind)", "A8 A9 A10 C7 C8 C9 G13 G27",
    ),
    "vendor authority": (
        r"(executor|executrix|administrator|probate|letters of administration|"
        r"grant of representation|power of attorney)", "A1 A7 G26",
    ),
}

DOC_PAGE_RE = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)", re.I)
# Footer of LANDATA imaged documents: "Plan LP021090 3" / "Plan TP458276N 1"
COVER_RE = re.compile(r"^(Plan|Instrument|Document)\s+\S+\s+(\d+)\s*$", re.M)
# Stamp / furniture lines that survive on an otherwise image-only page
NOISE_RE = re.compile(r"(Docusign Envelope ID|Delivered from the LANDATA|"
                      r"^\s*\d{1,3}\s*$)", re.I)


def norm(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def content_chars(text: str) -> int:
    """Characters that belong to the document, not to the e-sign stamp."""
    keep = [l for l in text.splitlines()
            if l.strip() and not NOISE_RE.search(l)]
    return len("\n".join(keep).strip())


def group_series(entries: list[dict]) -> list[list[dict]]:
    """Split Page X of Y occurrences into per-document runs."""
    entries = sorted(entries, key=lambda e: e["page"])
    runs: list[list[dict]] = []
    for e in entries:
        if runs and e["page"] == runs[-1][-1]["page"] + 1 \
                and e["first"] == runs[-1][-1]["first"] + 1 \
                and e["total"] == runs[-1][-1]["total"]:
            runs[-1].append(e)
        else:
            runs.append([e])
    return runs


def scan(path: str, min_chars: int = 50) -> dict:
    doc = pymupdf.open(path)
    image_only: list[int] = []
    page_chars: dict[int, int] = {}
    page_of: list[dict] = []
    covers: list[dict] = []
    hits: dict[str, list[dict]] = defaultdict(list)
    seen: set[tuple[str, int, str]] = set()

    compiled = {k: re.compile(v[0], re.I | re.M) for k, v in THEMES.items()}

    for i, page in enumerate(doc):
        n = i + 1
        text = page.get_text("text") or ""
        cc = content_chars(text)
        page_chars[n] = cc
        if cc < min_chars:
            image_only.append(n)
            continue

        m = DOC_PAGE_RE.search(text)
        if m:
            page_of.append({"page": n, "label": m.group(0),
                            "first": int(m.group(1)), "total": int(m.group(2))})
        for m in COVER_RE.finditer(text):
            try:
                total = int(m.group(2))
            except ValueError:
                continue
            if total > 1:
                covers.append({"page": n, "claims": total,
                               "kind": m.group(1)})

        for theme, rx in compiled.items():
            for m in rx.finditer(text):
                # line containing the match, for context
                start = text.rfind("\n", 0, m.start()) + 1
                end = text.find("\n", m.end())
                line = norm(text[start: end if end != -1 else len(text)])[:180]
                key = (theme, n, line)
                if key in seen or not line:
                    continue
                seen.add(key)
                hits[theme].append({"page": n, "line": line,
                                    "rows": THEMES[theme][1]})

    # A "Page X of Y" run is complete only when it covers 1..Y.
    incomplete: list[dict] = []
    for run in group_series(page_of):
        total = run[0]["total"]
        covered = {e["first"] for e in run}
        if len(run) < total or covered != set(range(1, total + 1)):
            missing = sorted(set(range(1, total + 1)) - covered)
            incomplete.append({
                "pages": [e["page"] for e in run],
                "label": f"pages {', '.join(map(str, sorted(covered)))} of {total} supplied",
                "missing": missing,
            })

    # A LANDATA cover sheet stating N pages must be followed by N pages — the
    # cover sheet itself is excluded from N ("Number of Pages (excluding this
    # cover sheet)"), so the document ends at page + N.
    multipage = []
    for c in covers:
        last = c["page"] + c["claims"]
        multipage.append({"page": c["page"], "claims": c["claims"],
                          "kind": c.get("kind", "document"),
                          "ends_at": last, "within_bundle": last <= len(doc)})

    return {
        "pdf": path,
        "pages": len(doc),
        "image_only_pages": image_only,
        "page_chars": page_chars,
        "incomplete_documents": incomplete,
        "multipage_claims": multipage,
        "hits": {k: v for k, v in hits.items()},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Scan a contract bundle for decisive clauses")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--min-chars", type=int, default=50,
                    help="pages with fewer characters are treated as image-only")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-hits", type=int, default=6, help="hits printed per theme")
    a = ap.parse_args()

    res = scan(a.pdf, a.min_chars)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    print(f"pdf: {res['pdf']}  pages={res['pages']}")
    io = res["image_only_pages"]
    print(f"\nIMAGE-ONLY PAGES ({len(io)}): {', '.join(f'p.{n}' for n in io) or 'none'}")
    if io:
        print("  → these carry no text; render them and read the drawing "
              "(easement labels, instrument numbers, dimensions):")
        rng = ",".join(str(n) for n in io[:20])
        print(f"    python render_pages.py --pdf \"{a.pdf}\" --pages {rng} --out pages_png")

    inc = res["incomplete_documents"]
    print(f"\nINCOMPLETE DOCUMENTS ({len(inc)}):")
    for d in inc[:10]:
        where = ", ".join(f"p.{p}" for p in d["pages"])
        print(f"  {where}: {d['label']} (missing {d['missing']})")
    if not inc:
        print("  none detected")

    multi = res.get("multipage_claims") or []
    if multi:
        print("\nMULTI-PAGE CLAIMS (check the pages are all there):")
        for c in multi[:10]:
            flag = "" if c["within_bundle"] else "  ← exceeds bundle"
            print(f"  p.{c['page']}: {c['kind']} of {c['claims']} pages "
                  f"(expected to end at p.{c['ends_at']}){flag}")

    print("\nCLAUSE HITS (feeds rows):")
    for theme, rows in THEMES.items():
        items = res["hits"].get(theme, [])
        print(f"\n[{theme}] → {rows[1]}   ({len(items)} hits)")
        for h in items[: a.max_hits]:
            print(f"  p.{h['page']:<3} {h['line']}")
        if len(items) > a.max_hits:
            print(f"  … {len(items) - a.max_hits} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
