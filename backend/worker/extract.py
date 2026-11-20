"""S3: LLM extraction of the A-G checklist model JSON.

Loads the skill's fixed checklist catalog (A-G item names + statutory notes) so
the model outputs exactly the 82 canonical rows, then calls the LLM in batches.
System prompt hard-codes the skill's "judgement red lines" from status-and-format.
"""
from __future__ import annotations

import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from . import llm
from .parse import ParsedDoc, SECTIONS

SKILL_DIR = ROOT / "skills" / "property-contract-review-report"
CATALOG = SKILL_DIR / "references" / "checklist-catalog.md"

SYSTEM_PROMPT = """You are a meticulous Australian conveyancing file reviewer. You produce a
Section 32 disclosure checklist review as STRICT JSON. Follow these rules exactly:

1. Output the exact checklist rows given by the user (fixed codes and item names).
   Never invent, drop, or renumber rows. If a matter truly does not arise, keep
   the row with provided="NA", verified="NA" and a contents line explaining why.
2. Every contents line MUST start with a short label then ": " then the value,
   and MUST carry the source PDF page as "(p.N)" or "(pp.N-M)". Example:
   "Title reference: Vol 1234 Fol 567 (p.12)". Never fabricate a page number or
   certificate/plan number. If you cannot locate evidence, set verified="Unclear"
   and write "Not located in reviewed pages".
3. COMPARISON ROWS DECIDE FIRST. For A1, A3, A4, A5, A7 and any other row that
   asks "does X agree with Y" or "is there a mortgage/caveat/overlay", the FIRST
   contents line must be the verdict, not raw evidence:
   - address/title/area agreement -> "Property consistency: MATCH" or "MISMATCH"
   - party names                    -> "Identity spelling: MATCH" or "MISMATCH"
   - existence of a thing           -> "Mortgage: NO", "Caveat: NONE", "Overlay: NONE"
   Evidence lines come AFTER the verdict. Enumerating "Contract Address: ...",
   "S32 Address: ...", "Title Address: ..." without a verdict line is INVALID —
   the reader must not have to compute the answer.
   A1 always needs BOTH "Property consistency: ..." and "Identity spelling: ...".
4. Name comparison is literal and pack-wide. A variant counts as a variant when
   the name appears in a real name field (Vendor / Registered proprietor /
   Purchaser / owner on a rates notice / Vendor on a State Revenue Office
   certificate). Punctuation, case, initials, a dropped middle name or an
   abbreviated surname are all MISMATCH -> verified="Action"; list every variant
   on its own line as "- <variant>: <which field> (p.N)". A string that is merely
   a search/reference code ("Client Reference: ...", "Your Reference: LD:...,
   ref ...") is NOT a name field — do not treat it as a spelling variant, and
   never treat a cover-page file reference as the vendor's name.
5. Two independent status columns:
   - provided: Present | Missing | Unclear | Awaiting | NA  (is the document in the pack?)
   - verified: Checked | Action | Unclear | NA  (is the content correct / needs action?)
6. JUDGEMENT RED LINES (never mark Checked when any applies -> use Action):
   - Any spelling variant of a party/vendor/registered proprietor name.
   - Vendor is an executor / selling an estate.
   - Grant/probate or other authority document is incomplete (e.g. "page 1 of 3").
   - A document is only cited/referenced but no copy is in the pack -> provided=Unclear, verified=Action.
   - A search result has replaced the actual certificate -> Unclear / Unclear.
   - Applicability (congestion levy / EPA / building certificate) cannot be confirmed -> Unclear.
   - A commercial planning report has replaced the s199 certificate. These reports
     usually state they are "NOT a Planning Certificate issued pursuant to Section
     199 of the Planning and Environment Act 1987" — that sentence means the pack
     has no s199 certificate: G8 is Unclear/Unclear, never Checked, and B3 must
     say the controls come from a report rather than a certificate.
   - The Special Conditions disclose works carried out without permits or no
     domestic building insurance ("renovation works ... in or about 2023",
     "the Vendor is unable to provide a building permit / domestic building
     insurance"). Those facts belong in D2/D3/D4 and G22/G23 as Action, or
     Missing/Action where the document is absent — never N/A, even when S32
     item 2.2 is blank.
6b. READ THE FORM, NOT ONLY THE PROSE. Section 32 is a checkbox form:
   - item 8 SERVICES: a service is UNCONNECTED only where its box carries an X;
     an unmarked box means connected. Write "Connected: Electricity, gas, water,
     sewerage." / "Not connected: Telephone. (p.41)". Do NOT read the list as
     "all of these are unconnected".
   - items 7.1/7.2, 10.1/10.2/10.3: an unmarked box means not applicable. Write
     "N/A - box unmarked (p.N)" with provided="Present", verified="NA" — the
     statement itself has answered. Reserve "N/A - explicit" for words that say so.
   - items 2.1/2.2 left blank do NOT mean the situation does not exist; check the
     Special Conditions before answering D1-D4 / G22 / G23.
   - attachment completeness: a cover letter saying "This statement includes ...
     Asset Plan ... Rates Certificate" is not proof the attachment is there. Turn
     to the actual pages and use the "N of M" / "Page X of Y" footers. A document
     that really is in the pack (an Asset Plan drawing page) is Present — not
     "cited but not located".
6c. GRANT IS NOT GUARANTEE. Only "Grant of Probate", "Grant of Representation"
   and "Letters of Administration" are authority documents. A "Form of Guarantee
   for Directors" clause is a guarantee, not a grant; citing it manufactures a
   false "missing authority document" finding.
6d. VALUE ROWS MUST CARRY THE VALUE, not a pointer to it.
   - A5: give the area and perimeter as printed in the council/planning report's
     SITE DIMENSIONS block ("Area: 705 sq. m", "Perimeter: 119 m (p.49)"), marked
     approximate if the source says so. "Boundaries: see plan" is not an answer.
   - A6: decide from the registered plan drawing. If nothing is shown there, write
     "Easements: None shown on the registered plan (pp.46-48)". "Refer to title"
     is a pointer, not a finding.
   - G5: a separate sewer map is N/A only because the asset plan already shows the
     sewer coverage — never because "the service is connected".
6e. VERDICT VOCABULARY IS CLOSED. In a value position use only MATCH, MISMATCH,
   YES, NO, NONE, N/A, Apply, or the literal wording from the document. Never
   invent CONFLICT, CHECKED, PARTIAL, UNKNOWN, PENDING. When two sources disagree,
   write BOTH facts as separate labelled lines and let verified="Action" carry the
   conflict:
     "Lease: YES - usage charges billed to a tenant (p.64)"
     "Contract: vacant possession stated (p.8)"
     "Review: confirm the tenancy ends before settlement"
   Never write "Disclosure status: CONFLICT".
6f. A11 ANSWERS ONE QUESTION: is there an existing failure to comply with an
   easement, covenant or other similar restriction? Building work without a permit
   is NOT a breach of an easement — it belongs in D2-D4 / G22 / G23. With no such
   failure, A11 reads "Non-compliance: None disclosed (p.39)" and stays Checked;
   do not park unrelated defects in it.
7. Consistency: the same fact across different rows must carry the same status
   and the same page references. Cross-check names, amounts and certificate numbers.
   A finding has ONE home row: the name-variant verdict lives in A1 (and G26 for
   authority); C10-C13 / G3 / G6 only note "Name variant: ... (p.N)" and stay
   Checked. Repeating Action across every row that mentions the person drowns the
   real findings. Summary rows use "See C10-C13 (pp.58, 64, 67)".
8. Preserve amounts, numbers, dates, place names and institution names VERBATIM.
   Do not convert, abbreviate or translate them.
9. Use <b> ONLY around verdict values (MATCH / MISMATCH / YES / NO) and amounts.
   Do not bold the leading label. List sub-items with "- ".
10. Return ONLY a JSON object: {"rows": [{"code","item","provided","verified","contents":[...],"notes"}]}
    with one entry per requested code, in the given order. No markdown, no prose.
"""


def parse_catalog() -> dict[str, dict[str, str]]:
    """Return {code: {item_en, item_zh, notes}} from the skill catalog."""
    text = CATALOG.read_text(encoding="utf-8")
    out: dict[str, dict[str, str]] = {}
    current_section = ""
    for line in text.splitlines():
        m_sec = re.match(r"^##\s+([A-G])\.\s+(.+)$", line)
        if m_sec:
            current_section = m_sec.group(1)
            continue
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        code = cells[0]
        if not re.fullmatch(r"[A-G]\d+", code):
            continue
        out[code] = {"section": current_section, "item": cells[1],
                     "item_zh": cells[2], "notes": cells[3]}
    return out


def section_heading(code: str) -> str:
    names = {
        "A": "A. PROPERTY & TITLE INFORMATION",
        "B": "B. PLANNING, ZONING & GOVERNMENT",
        "C": "C. RATES, CHARGES & OUTGOINGS",
        "D": "D. BUILDING, CONSTRUCTION & CONDITION",
        "E": "E. LEASES, OCCUPANCY & THIRD PARTY INTERESTS",
        "F": "F. RISKS, LITIGATION & SPECIAL MATTERS",
        "G": "G. REQUIRED DOCUMENTS & ATTACHMENTS",
    }
    return names.get(code[:1], code)


def batch_codes(catalog: dict[str, dict[str, str]]) -> dict[str, list[str]]:
    batches: dict[str, list[str]] = {s: [] for s in SECTIONS}
    for code in catalog:
        batches[code[:1]].append(code)
    return batches


PROVIDED_ENUM = ("Present", "Missing", "Unclear", "Awaiting", "NA")
VERIFIED_ENUM = ("Checked", "Action", "Unclear", "NA")

# The model occasionally answers "is the document provided?" with the *verified*
# vocabulary ("Checked") or a synonym. One stray value used to abort the whole
# report at S4 after several minutes of extraction, so the value is mapped back
# to the legal set here instead of being published unchecked or thrown away.
_PROVIDED_FIX = {
    "provided": "Present", "included": "Present", "attached": "Present", "yes": "Present",
    "available": "Present", "checked": "Present", "ok": "Present", "complete": "Present",
    "present in pack": "Present", "supplied": "Present",
    "not provided": "Missing", "absent": "Missing", "no": "Missing", "none": "Missing",
    "not included": "Missing", "not available": "Missing", "omitted": "Missing",
    "unknown": "Unclear", "ambiguous": "Unclear", "partial": "Unclear",
    "pending": "Awaiting", "requested": "Awaiting", "to be provided": "Awaiting",
    "n/a": "NA", "na": "NA", "not applicable": "NA", "notapplicable": "NA", "-": "NA",
}
_VERIFIED_FIX = {
    "ok": "Checked", "pass": "Checked", "satisfactory": "Checked", "present": "Checked",
    "verified": "Checked", "checked & clear": "Checked", "clear": "Checked",
    "fail": "Action", "issue": "Action", "problem": "Action", "follow up": "Action",
    "follow-up": "Action", "followup": "Action", "attention": "Action", "required": "Action",
    "unknown": "Unclear", "ambiguous": "Unclear", "partial": "Unclear", "unverified": "Unclear",
    "n/a": "NA", "na": "NA", "not applicable": "NA", "notapplicable": "NA", "-": "NA",
}


def _normalise(value, allowed, table, default: str = "Unclear") -> str:
    """Map a model-supplied enum value onto the legal set (case/space tolerant)."""
    s = str(value or "").strip().strip(".")
    low = s.lower()
    for a in allowed:
        if low == a.lower():
            return a
    if low in table:
        return table[low]
    return default


def _rows_prompt(codes: list[str], catalog: dict[str, dict[str, str]]) -> str:
    lines = []
    for c in codes:
        info = catalog[c]
        lines.append(f'- {c} | {info["item"]} | notes: {info["notes"]}')
    return "\n".join(lines)


def extract_batch(parsed: ParsedDoc, codes: list[str], catalog: dict[str, dict[str, str]],
                  **kwargs) -> list[dict]:
    page_text = parsed.batch_text(codes)
    user = (
        "Review the contract pack pages below and fill the following checklist rows.\n"
        "Return JSON {\"rows\":[...]} with exactly these codes in this order:\n"
        + _rows_prompt(codes, catalog)
        + "\n\n=== CONTRACT PACK TEXT ===\n" + page_text[:160000]
    )
    # The page selection is budgeted, so a certificate at the back of the pack may
    # not be in `page_text`. Identity comparison must not depend on that: hand the
    # A-section batch a deterministic index of every line containing the party
    # surname, and let it decide which hits are name fields and which are codes.
    if codes and codes[0][:1].upper() == "A":
        occ = parsed.name_occurrences()
        if occ:
            user += (
                "\n\n=== PARTY NAME OCCURRENCES (deterministic whole-pack scan) ===\n"
                "Every line below contains the registered proprietor's surname, including "
                "pages that are not in the pack text above. Judge each hit: a real name "
                "field (Vendor / Registered proprietor / Purchaser / rates-notice owner / "
                "SRO certificate vendor) counts towards the Identity spelling verdict; a "
                "reference string (ref ... / Client Reference / Your Reference / LD:...) "
                "does not. List every real variant on its own line with its page.\n"
                + "\n".join(occ))
    result = llm.extract_json([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ], **kwargs)
    rows = result.get("rows") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        raise ValueError("LLM output missing 'rows' array")
    # Force canonical code/item/notes from the catalog, never trust model text.
    clean = []
    for want, got in zip(codes, rows):
        clean.append({
            "code": want,
            "item": catalog[want]["item"],
            "provided": _normalise(got.get("provided"), PROVIDED_ENUM, _PROVIDED_FIX),
            "verified": _normalise(got.get("verified"), VERIFIED_ENUM, _VERIFIED_FIX),
            # Empty lines are a hard validation failure downstream, so drop them
            # here rather than failing a report over a stray "".
            "contents": [str(x).strip() for x in (got.get("contents") or []) if str(x).strip()],
            "notes": catalog[want]["notes"],
        })
    return clean


def extract_all(parsed: ParsedDoc, progress=None, workers: int = 4, **kwargs) -> dict:
    """Run all A-G batches and assemble the full skill model JSON skeleton.

    The seven sections are independent reads of the same document, so they run
    concurrently. Sequentially each batch costs minutes against a large model
    (100k+ chars of context), which put a whole contract at ~20 minutes and made
    the user think the job had hung. Concurrency bounds the wall clock to roughly
    the slowest single batch.

    `progress(done, total, section)` is called as each batch finishes so the
    worker can report live progress; it must never raise (wrapped below).
    """
    catalog = parse_catalog()
    batches = batch_codes(catalog)
    todo = [s for s in SECTIONS if batches[s]]
    results: dict[str, list[dict]] = {}
    lock = threading.Lock()

    def _one(sec: str) -> list[dict]:
        return extract_batch(parsed, batches[sec], catalog, **kwargs)

    if workers > 1 and len(todo) > 1:
        done = 0
        with ThreadPoolExecutor(max_workers=min(workers, len(todo)),
                                thread_name_prefix="extract") as ex:
            futs = {ex.submit(_one, s): s for s in todo}
            for fut in as_completed(futs):
                sec = futs[fut]
                results[sec] = fut.result()      # a failed batch fails the report
                with lock:
                    done += 1
                    if progress is not None:
                        try:
                            progress(done, len(todo), sec)
                        except Exception:   # progress reporting must never break extraction
                            pass
        sections = [{"heading": section_heading(s), "rows": results[s]} for s in todo]
    else:
        sections = []
        for idx, sec in enumerate(todo, start=1):
            rows = _one(sec)
            sections.append({"heading": section_heading(sec), "rows": rows})
            if progress is not None:
                try:
                    progress(idx, len(todo), sec)
                except Exception:  # progress reporting must never break extraction
                    pass
    address = ""
    m = re.search(r"Property:\s*(.+)", parsed.pages[0].text if parsed.pages else "", re.I)
    if m:
        address = m.group(1).strip()[:120]
    return {
        "lang": "en",
        "meta": {"address": address, "assessment_date": ""},
        "cards": {
            "left": {"title": "Overall review status", "badge_enum": "FollowUp",
                     "badge_label": "Follow-up required",
                     "notes": ["Provided = document availability.",
                               "Verified = content review result.",
                               "Notes are statutory references only."]},
            "right": {"title": "Status guide", "provided_title": "Provided",
                      "provided": ["Present", "Missing", "Unclear", "Awaiting", "NA"],
                      "verified_title": "Verified",
                      "verified": ["Checked", "Action", "Unclear", "NA"]},
        },
        "sections": sections,
        "followup": {"title": "Buyer key matters", "subheading": "Follow-up required",
                     "columns": ["Matter", "Key finding", "Next step", "PDF pages"], "rows": []},
        "other": {"subheading": "Other important property information",
                  "columns": ["Matter", "Key finding", "Buyer to consider", "PDF pages"], "rows": []},
        "bullets_heading": "Evidence citations & statutory notes",
        "bullets": [],
        "note_box": {"title": "Important", "body": "Document review only; not legal advice."},
        "footer": "Template v1.0 - auto-generated - not legal advice",
    }


if __name__ == "__main__":
    cat = parse_catalog()
    print("catalog rows:", len(cat), "by section:",
          {s: sum(1 for c in cat if c[:1] == s) for s in SECTIONS})
