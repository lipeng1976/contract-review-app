#!/usr/bin/env python3
"""Render a property contract review report (Section 32 checklist style) from a JSON model.

Usage:
    python build_review_report.py --model model.json --out report.pdf [--lang en|zh]

The model JSON mirrors the report structure produced for the Calista Avenue contract.
Rows carry canonical badge enums (for colour) plus optional display labels (for wording).
"""
import argparse
import json
import os
import re
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether, CondPageBreak,
)
from reportlab.lib.enums import TA_LEFT
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics

# ---------- palette ----------
BG = colors.HexColor("#F5F1EB")
CARD = colors.white
BORDER = colors.HexColor("#E3DCD0")
DARK = colors.HexColor("#1F2A37")
GREY = colors.HexColor("#5B6B7B")
NOTE_GREY = colors.HexColor("#6B7280")
ROW_ALT = colors.HexColor("#FBF8F3")
# Manually edited cells keep the exact same font/size as the original report,
# only italic + deep blue, so an edited export is visually the same document.
EDIT_COLOR_HEX = "#17366B"

# enum -> (bg, fg)
BADGE_COLOR = {
    "Present":   ("#E3F4E8", "#1E8A5A"),
    "Missing":   ("#FBE4E4", "#C0392B"),
    "Unclear":   ("#FBEED7", "#A97E13"),
    "Awaiting":  ("#FBE6D6", "#C87A2D"),
    "NA":        ("#EDF0F2", "#6B7280"),
    "Checked":   ("#E3F4E8", "#1E8A5A"),
    "Action":    ("#FCE6D8", "#B9532B"),
    "FollowUp":  ("#FBEED7", "#8A5A00"),
}

LABELS = {
    "en": {
        "Present": "Present", "Missing": "Missing", "Unclear": "Unclear",
        "Awaiting": "Awaiting", "NA": "N/A", "Checked": "Checked",
        "Action": "Action", "FollowUp": "Follow-up required",
        "head": ["#", "ITEM", "PROVIDED", "VERIFIED", "CONTENTS", "NOTES"],
    },
    "zh": {
        "Present": "已提供", "Missing": "缺失", "Unclear": "不明确",
        "Awaiting": "待提供", "NA": "不适用", "Checked": "已核对",
        "Action": "需跟进", "FollowUp": "需后续跟进",
        "head": ["#", "事项", "提供情况", "核验结果", "内容", "备注"],
    },
}

# Chinese faces. Helvetica has no CJK glyphs, so Chinese typed into an English
# report (manual edits, a Chinese answer) comes out as empty boxes unless a real
# CJK font is registered. Italic CJK does not exist; 楷体 (simkai) is the
# conventional stand-in and makes edited Chinese visibly distinct.
ZH_FONT = r"C:\Windows\Fonts\msyh.ttc"
ZH_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
ZH_ITALIC = r"C:\Windows\Fonts\simkai.ttf"
CJK_NAME = "CJK"
CJK_BOLD = "CJK-B"
CJK_ITALIC = "CJK-I"
_CJK_READY = False
_CJK_ITALIC_NAME = CJK_NAME


def _register_ttf(name, path, subfont=0):
    if not os.path.exists(path):
        return False
    try:
        pdfmetrics.registerFont(TTFont(name, path, subfontIndex=subfont))
        return True
    except Exception:
        return False


def register_cjk():
    """Register the CJK faces once. Safe to call for every language."""
    global _CJK_READY, _CJK_ITALIC_NAME
    if _CJK_READY:
        return True
    if not _register_ttf(CJK_NAME, ZH_FONT):
        # Some reportlab builds cannot read .ttc collections; fall back to a
        # single-file face so Chinese still renders instead of silently boxed.
        if not _register_ttf(CJK_NAME, r"C:\Windows\Fonts\simhei.ttf"):
            return False
    bold = CJK_BOLD if _register_ttf(CJK_BOLD, ZH_BOLD) else CJK_NAME
    _CJK_ITALIC_NAME = CJK_ITALIC if _register_ttf(CJK_ITALIC, ZH_ITALIC) else CJK_NAME
    pdfmetrics.registerFontFamily(CJK_NAME, normal=CJK_NAME, bold=bold,
                                  italic=_CJK_ITALIC_NAME, boldItalic=bold)
    _CJK_READY = True
    return True


def register_fonts(lang):
    # A Chinese report uses CJK for the whole body. An English report keeps
    # Helvetica, but still registers CJK so any Chinese an editor adds later
    # has a real glyph to fall back to.
    register_cjk()
    if lang == "zh" and _CJK_READY:
        return CJK_NAME, CJK_BOLD
    return "Helvetica", "Helvetica-Bold"


def make_styles(font, bold):
    S = {
        "title": ParagraphStyle("title", fontName=bold, fontSize=18, leading=22, textColor=DARK),
        "date": ParagraphStyle("date", fontName=font, fontSize=10, leading=13, textColor=GREY),
        "sect": ParagraphStyle("sect", fontName=bold, fontSize=13, leading=16, textColor=DARK,
                               spaceBefore=10, spaceAfter=4),
        "sub": ParagraphStyle("sub", fontName=bold, fontSize=11, leading=14, textColor=DARK,
                              spaceBefore=8, spaceAfter=4),
        "head": ParagraphStyle("head", fontName=bold, fontSize=7.6, leading=9.5, textColor=GREY),
        "code": ParagraphStyle("code", fontName=bold, fontSize=8.2, leading=11, textColor=DARK),
        "item": ParagraphStyle("item", fontName=font, fontSize=8.2, leading=11),
        "cont": ParagraphStyle("cont", fontName=font, fontSize=8.2, leading=11.2),
        "note": ParagraphStyle("note", fontName=font, fontSize=7.4, leading=9.5, textColor=NOTE_GREY),
        "cardh": ParagraphStyle("cardh", fontName=bold, fontSize=11, leading=14, textColor=DARK, spaceAfter=6),
        "cardb": ParagraphStyle("cardb", fontName=font, fontSize=8.6, leading=12.5, textColor=GREY),
        "badge": ParagraphStyle("badge", fontName=bold, fontSize=7, leading=9, alignment=1),
        "bullet": ParagraphStyle("bullet", fontName=font, fontSize=8.6, leading=12.5, leftIndent=12),
        "noteb": ParagraphStyle("noteb", fontName=font, fontSize=9.2, leading=13.5, textColor=DARK),
        "foot": ParagraphStyle("foot", fontName=font, fontSize=8, leading=10, textColor=NOTE_GREY),
    }
    return S


BULLET_RE = re.compile(r"^([-\u2022]\s+)?(.+)$", re.S)


def bold_lead(text):
    """Bold the leading key term of a CONTENTS line.

    Order of preference:
    1. a short label before a colon (ASCII or full-width), at most 6 words / 48 chars;
    2. otherwise the text before the first page reference (" (p." or "\uff08p.");
    3. otherwise the first word (CJK lines without separators stay unbolded).

    Lines that already start with markup are left untouched so the model can control
    emphasis when it matters.
    """
    m = BULLET_RE.match(text)
    mark, body = (m.group(1) or ""), m.group(2)
    if body.startswith("<"):
        return text
    colon = min([i for i in (body.find(":"), body.find("\uff1a")) if i >= 0], default=-1)
    cut = min([i for i in (body.find(" ("), body.find("\uff08")) if i >= 0], default=-1)
    if 0 < colon <= 48 and len(body[:colon].split()) <= 6:
        key, rest = body[:colon + 1], body[colon + 1:]
    elif 0 < cut <= 48:
        key, rest = body[:cut], body[cut:]
    elif " " in body:
        parts = body.split(" ", 1)
        key, rest = parts[0], " " + parts[1]
    else:
        return text
    return f"{mark}<b>{key}</b>{rest}"


# CJK ranges Helvetica cannot draw: radicals, unified ideographs, compatibility
# ideographs, full-width forms and CJK punctuation.
CJK_CLASS = "\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\u3000-\u303f"
CJK_RUN_RE = re.compile(f"([{CJK_CLASS}]+)")
HTML_TAG_RE = re.compile(r"(<[^>]+>)")


def _map_text_runs(text, wrap):
    """Apply wrap(part, is_cjk) to text runs without touching inline markup."""
    out = []
    for token in HTML_TAG_RE.split(text):
        if not token:
            continue
        if token.startswith("<") and token.endswith(">"):
            out.append(token)
            continue
        for part in CJK_RUN_RE.split(token):
            if part:
                out.append(wrap(part, bool(CJK_RUN_RE.fullmatch(part))))
    return "".join(out)


def cjk_safe(text):
    """Route CJK runs to a real font; leave Latin text and markup untouched."""
    if not text or not _CJK_READY or not CJK_RUN_RE.search(text):
        return text
    return _map_text_runs(
        text, lambda part, is_cjk: '<font name="%s">%s</font>' % (CJK_NAME, part) if is_cjk else part)


def mark_edited(text: str) -> str:
    """Wrap a cell's inline markup: identical font/size, italic + deep blue only.

    Chinese runs need a CJK face (Helvetica has no glyphs for them); 楷体 is the
    italic stand-in. Latin runs keep the original Helvetica-Oblique.
    """
    if not _CJK_READY or not CJK_RUN_RE.search(text):
        return f'<font color="{EDIT_COLOR_HEX}"><i>{text}</i></font>'

    def wrap(part, is_cjk):
        if is_cjk:
            return '<font name="%s" color="%s">%s</font>' % (
                _CJK_ITALIC_NAME, EDIT_COLOR_HEX, part)
        return f'<font color="{EDIT_COLOR_HEX}"><i>{part}</i></font>'

    return _map_text_runs(text, wrap)


def badge(enum, label, styles, width=46, is_edited=False):
    bg, fg = BADGE_COLOR.get(enum, BADGE_COLOR["NA"])
    if is_edited:
        fg = EDIT_COLOR_HEX
        label = mark_edited(label)
    else:
        label = cjk_safe(label)
    p = Paragraph(label, ParagraphStyle("b", parent=styles["badge"], textColor=colors.HexColor(fg)))
    t = Table([[p]], colWidths=[width])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(bg)),
        ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor(bg)),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def badge_row(enums, labels, styles, width=44):
    cells = [badge(e, labels.get(e, e), styles, width=width) for e in enums]
    t = Table([cells], colWidths=[width] * len(enums))
    t.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 1), ("RIGHTPADDING", (0, 0), (-1, -1), 1),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def checklist_table(rows, styles, labels, head):
    data = [[Paragraph(h, styles["head"]) for h in head]]
    for r in rows:
        # Optional per-row marker set by the edited-report export. Rows produced
        # by the normal worker never carry it, so their PDF is byte-identical.
        ed = r.get("_edited") or {}
        code_text = mark_edited(r["code"]) if ed.get("code") else cjk_safe(r["code"])
        item_text = mark_edited(r["item"]) if ed.get("item") else cjk_safe(r["item"])
        contents = [mark_edited(bold_lead(c)) if ed.get("contents") else cjk_safe(bold_lead(c))
                    for c in r["contents"]]
        note_text = r.get("notes") or "-"
        if ed.get("notes"):
            note_text = mark_edited(note_text)
        else:
            note_text = cjk_safe(note_text)
        data.append([
            Paragraph(code_text, styles["code"]),
            Paragraph(item_text, styles["item"]),
            badge(r["provided"], r.get("provided_label") or labels.get(r["provided"], r["provided"]),
                  styles, is_edited=bool(ed.get("provided"))),
            badge(r["verified"], r.get("verified_label") or labels.get(r["verified"], r["verified"]),
                  styles, is_edited=bool(ed.get("verified"))),
            Paragraph("<br/>".join(contents), styles["cont"]),
            Paragraph(note_text, styles["note"]),
        ])
    cols = [30, 96, 50, 50, 254, 60]
    t = Table(data, colWidths=cols, repeatRows=1)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("BACKGROUND", (0, 0), (-1, 0), ROW_ALT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), ROW_ALT))
    t.setStyle(TableStyle(style))
    return t


def info_table(block, styles, widths):
    data = [[Paragraph(cjk_safe(h), styles["head"]) for h in block["columns"]]]
    for r in block["rows"]:
        data.append([
            Paragraph(cjk_safe(r.get("matter", "")), styles["item"]),
            Paragraph(cjk_safe("<br/>".join(r.get("finding") or [])), styles["cont"]),
            Paragraph(cjk_safe("<br/>".join(r.get("action") or [])), styles["cont"]),
            Paragraph(cjk_safe(r.get("pages") or ""), styles["note"]),
        ])
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("BACKGROUND", (0, 0), (-1, 0), ROW_ALT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--lang", choices=["en", "zh"], default="en")
    args = ap.parse_args()

    with open(args.model, encoding="utf-8") as f:
        model = json.load(f)

    lang = model.get("lang", args.lang)
    font, bold = register_fonts(lang)
    S = make_styles(font, bold)
    labels = LABELS[lang]
    meta = model.get("meta", {})

    # Chinese typed into an English report must not fall back to Helvetica's
    # empty glyphs; every free-text paragraph goes through the CJK fallback.
    P = lambda text, style, **kw: Paragraph(cjk_safe(text), style, **kw)

    PAGE_W, PAGE_H = letter
    MARGIN = 36
    AVAIL = PAGE_W - 2 * MARGIN

    def on_page(canvas, _doc):
        canvas.saveState()
        canvas.setFillColor(BG)
        canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
        canvas.restoreState()

    doc = BaseDocTemplate(args.out, pagesize=letter, leftMargin=MARGIN, rightMargin=MARGIN,
                         topMargin=MARGIN, bottomMargin=MARGIN)
    frame = Frame(MARGIN, MARGIN, AVAIL, PAGE_H - 2 * MARGIN, id="main")
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=on_page)])

    story = []
    story.append(P(meta["address"], S["title"]))
    if meta.get("assessment_date"):
        story.append(Spacer(1, 4))
        story.append(P(meta["assessment_date"], S["date"]))
    story.append(Spacer(1, 10))

    cards = model.get("cards", {})
    left = cards.get("left", {})
    left_card = [P(left.get("title", ""), S["cardh"])]
    if left.get("badge_enum"):
        left_card.append(badge(left["badge_enum"],
                               left.get("badge_label") or labels.get(left["badge_enum"], left["badge_enum"]),
                               S, width=116))
        left_card.append(Spacer(1, 6))
    for n in left.get("notes", []):
        left_card.append(P(n, S["cardb"]))

    right = cards.get("right", {})
    right_card = [P(right.get("title", ""), S["cardh"])]
    if right.get("provided_title"):
        right_card.append(P(f"<b>{right['provided_title']}</b>", S["cardb"]))
        right_card.append(badge_row(right.get("provided", []), labels, S))
    if right.get("verified_title"):
        right_card.append(Spacer(1, 6))
        right_card.append(P(f"<b>{right['verified_title']}</b>", S["cardb"]))
        right_card.append(badge_row(right.get("verified", []), labels, S))

    cards_t = Table([[left_card, right_card]], colWidths=[AVAIL / 2 - 6, AVAIL / 2 - 6])
    cards_t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CARD),
        ("BOX", (0, 0), (0, 0), 0.5, BORDER), ("BOX", (1, 0), (1, 0), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 12), ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(cards_t)
    story.append(Spacer(1, 10))

    for sect in model.get("sections", []):
        # keep a section heading together with at least the header row + one item row
        story.append(CondPageBreak(110))
        story.append(KeepTogether([P(sect["heading"], S["sect"]),
                     HRFlowable(width="100%", thickness=0.6, color=BORDER, spaceAfter=6)]))
        story.append(checklist_table(sect["rows"], S, labels, labels["head"]))
        story.append(Spacer(1, 10))

    widths = [110, 190, 170, 70]
    if model.get("followup"):
        fu = model["followup"]
        story.append(CondPageBreak(100))
        story.append(P(fu.get("title", ""), S["sect"]))
        story.append(HRFlowable(width="100%", thickness=0.6, color=BORDER, spaceAfter=6))
        if fu.get("subheading"):
            story.append(P(fu["subheading"], S["sub"]))
        story.append(info_table(fu, S, widths))
        story.append(Spacer(1, 10))

    if model.get("other"):
        ot = model["other"]
        story.append(CondPageBreak(150))
        if ot.get("subheading"):
            story.append(P(ot["subheading"], S["sub"]))
        story.append(info_table(ot, S, widths))
        story.append(Spacer(1, 10))

    if model.get("bullets"):
        story.append(P(model["bullets_heading"], S["sect"]))
        story.append(HRFlowable(width="100%", thickness=0.6, color=BORDER, spaceAfter=6))
        for b in model["bullets"]:
            story.append(P("&bull;&nbsp;&nbsp;" + b, S["bullet"]))
            story.append(Spacer(1, 3))
        story.append(Spacer(1, 14))

    nb = model.get("note_box")
    if nb:
        note_box = Table([[[
            P(nb["title"], ParagraphStyle("nbh", parent=S["cardh"])),
            P(nb["body"], S["noteb"]),
        ]]], colWidths=[AVAIL])
        note_box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FDF3E0")),
            ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor("#B8600B")),
            ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ]))
        story.append(note_box)
        story.append(Spacer(1, 16))

    if model.get("footer"):
        story.append(P(model["footer"], S["foot"]))

    doc.build(story)
    print("saved:", args.out)


if __name__ == "__main__":
    main()
