#!/usr/bin/env python3
"""Render chosen PDF pages to PNG so plans and imaged folios can actually be read.

Plans, imaged folios and scanned certificate pages have no text layer. An agent
that only reads text will report "no easement identified" for a page that clearly
says `E-1 — EASEMENT TO SHIRE OF MULGRAVE CREATED BY C/E A881068`, and the report
then loses the only hard evidence on the row.

    python render_pages.py --pdf pack.pdf --pages 46-50,58 --out pages_png
    python render_pages.py --pdf pack.pdf --image-only --out pages_png

Prints the file written for each page, so the caller can open them in order.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

try:
    import pymupdf
except ImportError:  # pragma: no cover
    try:
        import fitz as pymupdf  # type: ignore
    except ImportError:
        print("PyMuPDF required: pip install pymupdf", file=sys.stderr)
        sys.exit(2)


def parse_pages(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*-\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            out.extend(range(min(a, b), max(a, b) + 1))
        elif part.isdigit():
            out.append(int(part))
        else:
            raise ValueError(f"bad page spec: {part!r}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Render PDF pages to PNG")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--pages", help="e.g. 46-50,58")
    ap.add_argument("--image-only", action="store_true",
                    help="render every page whose text layer is effectively empty")
    ap.add_argument("--min-chars", type=int, default=50)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--prefix", default="p")
    a = ap.parse_args()

    doc = pymupdf.open(a.pdf)
    if a.image_only:
        pages = [i + 1 for i, p in enumerate(doc)
                 if len((p.get_text("text") or "").strip()) < a.min_chars]
    elif a.pages:
        pages = parse_pages(a.pages)
    else:
        print("need --pages or --image-only", file=sys.stderr)
        return 2

    pages = [p for p in pages if 1 <= p <= len(doc)]
    if not pages:
        print("no pages to render", file=sys.stderr)
        return 1

    os.makedirs(a.out, exist_ok=True)
    for n in pages:
        pix = doc[n - 1].get_pixmap(dpi=a.dpi)
        path = os.path.join(a.out, f"{a.prefix}{n:02d}.png")
        pix.save(path)
        print(f"{path}  {pix.width}x{pix.height}")
    print(f"\n{len(pages)} page(s) rendered at {a.dpi} dpi → {a.out}")
    print("Read these before writing A5, A6, A11, G1 and G2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
