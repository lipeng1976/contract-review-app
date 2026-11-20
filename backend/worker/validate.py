"""S4: validate the extracted model JSON before rendering / publishing.

Hard failures: missing sections, duplicate/unknown codes, wrong row count,
invalid status enums, malformed page references.
Soft findings: Action/Unclear on critical rows -> gate flag for admin review.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SECTIONS = tuple("ABCDEFG")
PROVIDED_ENUM = {"Present", "Missing", "Unclear", "Awaiting", "NA"}
VERIFIED_ENUM = {"Checked", "Action", "Unclear", "NA"}
PAGE_RE = re.compile(r"pp?\.?\s*\d+(?:\s*[-–—]\s*\d+)?", re.I)

# Rows that must never be auto-published as Checked without human review.
CRITICAL_CODES = {"A1", "A3", "A4", "A6", "A7", "E1", "G2"}

# Rows whose whole point is a verdict ("does this agree?", "is there a mortgage?").
# A contents list that merely enumerates field values — "Contract Address: ...",
# "S32 Address: ...", "Title Address: ..." — is formally well-formed but decides
# nothing: the reader still has to work out the answer, and nothing downstream can
# tell that shape apart from a real finding. That is exactly what the extractor
# produced for A1, so A1 is a hard failure. The other comparison rows are only
# counted, not rejected: rows like A3/A5 legitimately answer with a value
# ("Registered proprietor: ..."), and failing those would throw away a whole
# report over a wording preference.
VERDICT_STRICT_CODES = {"A1"}
VERDICT_SOFT_CODES = {"A3", "A4", "A5", "A7", "B1", "B3", "B4", "B5", "B7"}
VERDICT_RE = re.compile(r"\b(MATCH|MISMATCH|YES|NO|NONE|APPLY|N/A)\b", re.I)
# A1 carries one verdict per question it asks; both are mandatory.
A1_REQUIRED = ("property consistency", "identity spelling")


@dataclass
class Validation:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    gate_review: bool = False
    gate_reasons: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def validate(model: dict, expected_codes: dict[str, dict] | None = None) -> Validation:
    v = Validation()
    sections = model.get("sections")
    if not isinstance(sections, list) or not sections:
        v.errors.append("model.sections missing or empty")
        v.ok = False
        return v

    seen: dict[str, str] = {}
    total = 0
    action = unclear = 0
    page_refs = 0
    no_label_lines = 0
    no_verdict = 0
    no_verdict_soft = 0
    for sec in sections:
        heading = str(sec.get("heading", ""))[:1].upper()
        if heading not in SECTIONS:
            v.errors.append(f"unknown section heading: {sec.get('heading')!r}")
            v.ok = False
            continue
        rows = sec.get("rows", [])
        if not isinstance(rows, list):
            v.errors.append(f"section {heading} rows not a list")
            v.ok = False
            continue
        for r in rows:
            total += 1
            code = str(r.get("code", "")).strip()
            if not re.fullmatch(r"[A-G]\d+", code):
                v.errors.append(f"invalid code {code!r}")
                v.ok = False
            if code in seen:
                v.errors.append(f"duplicate code {code} (also in {seen[code]})")
                v.ok = False
            seen[code] = heading
            if expected_codes and code not in expected_codes:
                v.errors.append(f"unknown code {code} not in skill catalog")
                v.ok = False
            prov = str(r.get("provided", ""))
            ver = str(r.get("verified", ""))
            if prov not in PROVIDED_ENUM:
                v.errors.append(f"{code} bad provided={prov!r}")
                v.ok = False
            if ver not in VERIFIED_ENUM:
                v.errors.append(f"{code} bad verified={ver!r}")
                v.ok = False
            contents = r.get("contents", [])
            if not isinstance(contents, list):
                v.errors.append(f"{code} contents not a list")
                v.ok = False
            else:
                for line in contents:
                    if not isinstance(line, str) or not line.strip():
                        v.errors.append(f"{code} empty/non-string contents line: {str(line)[:60]}")
                        v.ok = False
                        continue
                    if ":" not in line:
                        # Skill renderer supports lines without a label (it bolds
                        # the first keyword). Not a hard failure; just tracked.
                        no_label_lines += 1
                    if PAGE_RE.search(str(line)):
                        page_refs += 1
                flat = [str(x) for x in contents if isinstance(x, str) and x.strip()]
                if flat and not VERDICT_RE.search(flat[0]):
                    if code in VERDICT_STRICT_CODES:
                        no_verdict += 1
                        v.errors.append(
                            f"{code} first contents line has no verdict "
                            f"(MATCH/MISMATCH): {flat[0][:80]!r}")
                        v.ok = False
                    elif code in VERDICT_SOFT_CODES:
                        no_verdict_soft += 1
                if code == "A1" and flat:
                    joined = " ".join(flat).lower()
                    for label in A1_REQUIRED:
                        if label not in joined:
                            no_verdict += 1
                            v.errors.append(f"A1 missing '{label}' verdict line")
                            v.ok = False
            if ver == "Action":
                action += 1
            elif ver == "Unclear":
                unclear += 1
            if code in CRITICAL_CODES and ver in ("Action", "Unclear"):
                v.gate_review = True
                v.gate_reasons.append(f"{code}={ver}")

    if expected_codes:
        missing = set(expected_codes) - set(seen)
        if missing:
            v.errors.append(f"missing {len(missing)} codes: {sorted(missing)[:12]}")
            v.ok = False

    v.stats = {"rows": total, "sections": len(sections), "action": action,
               "unclear": unclear, "page_refs": page_refs,
               "unique_codes": len(seen), "no_label_lines": no_label_lines,
               "no_verdict": no_verdict, "no_verdict_soft": no_verdict_soft}
    if action or unclear:
        v.gate_review = True
        if not v.gate_reasons:
            v.gate_reasons.append(f"{action} Action + {unclear} Unclear rows")
    return v
