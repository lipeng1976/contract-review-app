"""S1/S2: PDF parsing and section batching.

- Extracts per-page text with PyMuPDF.
- Detects image-only pages (no meaningful text layer) -> flagged for vision.
- Renders flagged pages to PNG (base64) for the vision model.
- Batches pages by A-G section hints for per-section extraction.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

SECTIONS = tuple("ABCDEFG")

# Broad patterns that mark a title/certificate pack page. Used as a fallback
# priority signal only — the real selection is per section (SECTION_KEYWORDS),
# because a single shared union sent every section the same 34 pages and left the
# certificates at the back of the pack unread.
SECTION_HINT_RE = re.compile(
    r"\b(?:section\s*32|title\s+search|planning|zoning|bushfire|"
    r"council\s+rates|water\s*[/&]?\s*sewer|owners\s+corporation|"
    r"easement|covenant|mortgage|caveat|probate|grant\s+of\s+probate|"
    r"contract\s+of\s+sale|particulars\s+of\s+residential\s+property)\b", re.I)

# Per-section evidence patterns. A page that matches the section's own pattern is
# put ahead of the shared fallback pages, so C gets the rates notices and G gets
# the clearance certificates even though they sit at the end of the pack. Without
# this the 40-page cap silently cut everything after p.40 and, for example, the
# vendor's abbreviated name on the SRO certificates was never in view.
SECTION_KEYWORDS = {
    # Deliberately narrow: "contract of sale" and a bare "vendor" are on almost
    # every page of the general conditions, and a wide pattern here pushed the
    # title search and the property report out of the A batch's character budget.
    # A needs the particulars, the Section 32 statement and the title search only.
    "A": r"particulars of sale|section 32 statement|register search statement|"
         r"title search|registered proprietor|volume \d+\s*folio|lot \d+ on plan|"
         r"street address|vendor'?s?\s+name|property report|site dimensions|land area",
    "B": r"planning|zoning|\bzone\b|overlay|bushfire|heritage|flood|vicroads|"
         r"aboriginal|electorate|planning property report|covenant",
    "C": r"council rates|rate certificate|rates notice|land tax|water|sewer|"
         r"assessment no|yarra valley|outstanding|vacant residential|windfall|"
         r"\bwgt\b|congestion levy",
    "D": r"building permit|occupancy permit|owner builder|building certificate|"
         r"bushfire attack|defects|warranty|insurance",
    "E": r"\blease\b|tenancy|tenant|bond|licence|license|occupancy agreement|\brent\b",
    "F": r"litigation|notice|order|caveat|dispute|owners corporation|proceedings|"
         r"infringement|environment protection",
    "G": r"certificate|clearance|property report|diagram|landata|state revenue|"
         r"commissioner|attachment|schedule|asset plan|information statement|"
         r"water act|yarra valley|melbourne water|sewer",
}

# Per-batch context budget, in characters. Measured: qwen3.5-plus handled ~100k
# characters in ~120s, so 120k stays inside the 300s timeout while leaving room
# for a 72-page pack to be represented beyond its first 40 pages.
BATCH_CHAR_BUDGET = 120000

MIN_TEXT_CHARS = 40  # below this a page is considered image-only / scanned


@dataclass
class Page:
    number: int
    text: str
    images: int
    image_only: bool
    png_b64: str | None = None


@dataclass
class ParsedDoc:
    source: str
    pages: list[Page] = field(default_factory=list)

    @property
    def all_text(self) -> str:
        return "\n".join(f"===== PAGE {p.number} =====\n{p.text}" for p in self.pages)

    @property
    def image_only_pages(self) -> list[Page]:
        return [p for p in self.pages if p.image_only]

    def doc_groups(self) -> dict[int, tuple[int, ...]]:
        """Map page number -> the whole numbered attachment it belongs to.

        A water statement arrives as seven pages: a cover letter (which names the
        Asset Plan), the W.A. 1989 statement, the Melbourne Water statement, the
        asset-plan drawing, the rates certificate, and so on. Only the cover page
        carries words that pattern-match a checklist section, so page-by-page
        selection kept the letter and dropped the drawing — and the report then
        said "Asset Plan: cited but not located".

        Grouping by the "N of M" footer keeps an attachment whole regardless of
        what its inner pages happen to say.
        """
        seq: list[tuple[int, int, int]] = []
        for page in self.pages:
            tail = page.text[-300:] if len(page.text) > 300 else page.text
            m = re.findall(r"\b(\d{1,2})\s+of\s+(\d{1,2})\b", tail)
            if m:
                seq.append((page.number, int(m[-1][0]), int(m[-1][1])))

        groups: dict[int, tuple[int, ...]] = {}

        def flush(run: list[tuple[int, int, int]]) -> None:
            # "1 of 1" appears on every single-page certificate; only real
            # multi-page runs are worth treating as one unit.
            if len(run) < 2:
                return
            ids = tuple(x[0] for x in run)
            for pid in ids:
                groups[pid] = ids

        run: list[tuple[int, int, int]] = []
        for item in seq:
            if run and (
                item[1] == 1
                or item[2] != run[-1][2]
                or item[1] != run[-1][1] + 1
                or item[0] != run[-1][0] + 1
            ):
                flush(run)
                run = []
            run.append(item)
        flush(run)
        return groups

    def _fit(self, pages: list[int], limit: int) -> list[int]:
        """Take pages in order until the character budget is spent, never
        splitting a numbered attachment in the middle."""
        groups = self.doc_groups()
        picked: list[int] = []
        used = 0
        done: set[tuple[int, ...]] = set()
        for n in pages:
            grp = groups.get(n)
            if grp is not None:
                if grp in done:
                    continue
                done.add(grp)
                size = sum(len(self.pages[x - 1].text) for x in grp)
                if picked and used + size > limit:
                    continue
                picked.extend(grp)
                used += size
                continue
            size = len(self.pages[n - 1].text)
            if picked and used + size > limit:
                continue        # a later, shorter page may still fit
            picked.append(n)
            used += size
        return picked

    def batch_text(self, codes: list[str], budget: int | None = None) -> str:
        """Pick the pages that matter for one section, in document order.

        Priority: contract front matter -> pages matching this section's own
        pattern (plus every page of any numbered attachment those pages belong
        to) -> pages matching the shared hint pattern -> everything else, up to
        the character budget. The old version kept the first 40 pages by number,
        which cut the entire back half of a 72-page pack (rates notices, water
        certificates, SRO clearances) for every section — and sent all seven
        sections the exact same 34 pages.
        """
        sec = (codes[0][:1] if codes else "A").upper()
        pat = SECTION_KEYWORDS.get(sec)
        own_re = re.compile(pat, re.I) if pat else None
        seed = [p.number for p in self.pages[:3]]
        own = [p.number for p in self.pages if own_re and own_re.search(p.text)]
        groups = self.doc_groups()

        base: set[int] = set(seed) | set(own)
        for n in list(base):
            base.update(groups.get(n, ()))
        base_list = sorted(base)

        hinted = [p.number for p in self.pages if SECTION_HINT_RE.search(p.text)]
        tail = [p.number for p in self.pages if p.number not in base]
        limit = budget or BATCH_CHAR_BUDGET

        picked = (list(base_list) if sum(len(self.pages[n - 1].text) for n in base_list)
                  <= int(limit * 1.6) else self._fit(base_list, limit))
        used = sum(len(self.pages[n - 1].text) for n in picked)
        for n in self._fit(hinted + tail, max(limit - used, 0)):
            if n not in picked:
                picked.append(n)
        return "\n".join(
            f"===== PAGE {n} =====\n{self.pages[n - 1].text}" for n in sorted(picked))

    def party_names(self) -> list[str]:
        """Full party names from the two authoritative fields.

        Title search REGISTERED PROPRIETOR block, plus the Section 32 vendor
        block. Only used to drive the surname sweep below, so a miss costs
        nothing beyond a skipped scan.
        """
        stop = {"ESTATE", "FEE", "SIMPLE", "SOLE", "PROPRIETOR", "REGISTERED",
                "VENDOR", "MORTGAGE", "PURCHASER", "OF", "THE"}

        def _clean(line: str) -> str:
            s = re.split(r"\s+of\s+", line.strip())[0].strip().rstrip(",")
            if not re.fullmatch(r"[A-Z][A-Z'\-\. ]{4,60}", s):
                return ""
            tokens = s.split()
            if not 2 <= len(tokens) <= 4 or any(t in stop for t in tokens):
                return ""
            return " ".join(tokens)

        names: list[str] = []
        # The heading must stand alone on its line: "registered proprietor" also
        # appears mid-sentence in the contract boilerplate ("you become the
        # registered proprietor"), which is not a name field.
        heading = re.compile(r"^REGISTERED\s+PROPRIETOR\s*$", re.M)
        for p in self.pages:
            m = heading.search(p.text or "")
            if not m:
                continue
            for line in (p.text or "")[m.end():].splitlines()[:7]:
                got = _clean(line)
                if got:
                    names.append(got)
                    break
            if names:
                return list(dict.fromkeys(names))
        # Fallback: the Section 32 vendor block.
        s32 = re.compile(r"Vendor['’]s\s+name", re.I)
        for p in self.pages:
            m = s32.search(p.text or "")
            if not m:
                continue
            for line in (p.text or "")[m.end():].splitlines()[:15]:
                got = _clean(line)
                if got:
                    names.append(got)
                    break
            if names:
                break
        return list(dict.fromkeys(names))

    def name_occurrences(self, limit: int = 45) -> list[str]:
        """Every line in the pack containing the party surname.

        A batch only receives the pages its section selected, so a name variant
        printed on a rates notice or a State Revenue Office certificate can sit in
        a page that never reached the model — and "Identity spelling: MATCH" then
        gets asserted from two pages out of seventy-two. This sweep makes the
        variants visible regardless of page selection; the model still decides
        whether each hit is a name field or a reference string.
        """
        names = self.party_names()
        if not names:
            return []
        surname = names[0].split()[-1]
        rx = re.compile(r"\b" + re.escape(surname) + r"\b", re.I)
        out: list[str] = []
        seen: set[str] = set()
        for p in self.pages:
            for line in (p.text or "").splitlines():
                line = " ".join(line.split())
                if not line or not rx.search(line):
                    continue
                entry = f"p.{p.number}: {line[:140]}"
                if entry in seen:
                    continue
                seen.add(entry)
                out.append(entry)
        return out[:limit]


def parse_pdf(path: str | Path, render_images: bool = True,
              max_image_pages: int = 20) -> ParsedDoc:
    doc = pymupdf.open(str(path))
    out = ParsedDoc(source=str(path))
    for i, page in enumerate(doc):
        text = page.get_text("text") or ""
        imgs = len(page.get_images(full=True))
        image_only = len(text.strip()) < MIN_TEXT_CHARS
        p = Page(number=i + 1, text=text, images=imgs, image_only=image_only)
        if image_only and render_images and len(out.image_only_pages) < max_image_pages:
            pix = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
            p.png_b64 = base64.b64encode(pix.tobytes("png")).decode("ascii")
        out.pages.append(p)
    doc.close()
    return out


def write_text_cache(parsed: ParsedDoc, tmp_dir: Path) -> Path:
    """Persist per-page text + summary to tmp for debugging / S2 traceability."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    summary = [f"pages: {len(parsed.pages)}",
               f"image_only: {len(parsed.image_only_pages)}"]
    for p in parsed.pages:
        summary.append(f"p{p.number:02d} chars={len(p.text.strip()):5d} images={p.images}"
                       + (" [IMAGE-ONLY]" if p.image_only else ""))
        (tmp_dir / f"p{p.number:02d}.txt").write_text(p.text, encoding="utf-8")
    (tmp_dir / "page_summary.txt").write_text("\n".join(summary), encoding="utf-8")
    return tmp_dir / "page_summary.txt"


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m worker.parse <pdf> [tmp_dir]")
        raise SystemExit(2)
    d = parse_pdf(sys.argv[1])
    print("pages:", len(d.pages), "image-only:", [p.number for p in d.image_only_pages])
    if len(sys.argv) > 2:
        write_text_cache(d, Path(sys.argv[2]))
