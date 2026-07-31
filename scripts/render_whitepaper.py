"""Render docs/WHITEPAPER.md into a styled PDF (docs/Syrudas-AI-Whitepaper.pdf).

Purpose-built for this document's markdown subset: title, ## sections,
paragraphs, fenced code blocks, pipe tables, bullet/numbered lists, inline
bold/italic/code. Rerun after editing the whitepaper.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    CondPageBreak, HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer,
    Table, TableStyle, XPreformatted,
)
from reportlab.platypus.tableofcontents import TableOfContents

# Body text is set at 11.5pt inside 1.25in margins, which puts a line at about
# 78 characters. The previous 10pt/0.9in setting measured ~100 characters per
# line - well past the 50-75 that reads comfortably, and the single biggest
# readability problem in the document regardless of vocabulary.
MARGIN = 1.25 * inch
BODY_SIZE = 11.5

# A heading needs enough room beneath it to be worth starting: its own block is
# ~39pt, so this leaves at least three lines of body text. Below that, break to
# the next page rather than stranding the heading at the foot of this one.
# Deliberately not reportlab's keepWithNext, which binds the heading to the
# WHOLE next paragraph and pushes headings off pages that had ample room.
SECTION_HEADROOM = 1.25 * inch

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "WHITEPAPER.md"
OUT = ROOT / "docs" / "Syrudas-AI-Whitepaper.pdf"

# the cover version is substituted at render time rather than hand-typed: a
# stale number on a shipped PDF is exactly the kind of drift the document
# claims not to have
sys.path.insert(0, str(ROOT))
from server.config import APP_VERSION  # noqa: E402

INK = colors.HexColor("#1c2333")
ACCENT = colors.HexColor("#3b5fc0")
DIM = colors.HexColor("#5a6172")
CODE_BG = colors.HexColor("#f2f4f8")
BORDER = colors.HexColor("#c9cfdb")

BASE = dict(fontName="Helvetica", fontSize=BODY_SIZE, leading=17, textColor=INK)
STYLES = {
    "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=21,
                            leading=26, textColor=INK, spaceAfter=4),
    "meta": ParagraphStyle("meta", fontName="Helvetica", fontSize=10.5,
                           leading=14, textColor=DIM, spaceAfter=10),
    "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=14.5,
                         leading=18, textColor=ACCENT, spaceBefore=18, spaceAfter=7),
    # Ragged right rather than justified: without hyphenation, justifying this
    # measure opens rivers of white space and irregular word gaps, which costs
    # more legibility than the tidy right edge buys.
    # allowWidows/allowOrphans=0: never leave a paragraph's first or last line
    # alone on the far side of a page break.
    "body": ParagraphStyle("body", spaceAfter=9,
                           allowWidows=0, allowOrphans=0, **BASE),
    "bullet": ParagraphStyle("bullet", leftIndent=18, bulletIndent=5,
                             spaceAfter=6, allowWidows=0, allowOrphans=0, **BASE),
    "code": ParagraphStyle("code", fontName="Courier", fontSize=7.4, leading=9.2,
                           textColor=INK, backColor=CODE_BG, borderColor=BORDER,
                           borderWidth=0.6, borderPadding=7, spaceBefore=4, spaceAfter=8),
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=9.5,
                           leading=13, textColor=INK),
    "cellhead": ParagraphStyle("cellhead", fontName="Helvetica-Bold", fontSize=9.5,
                               leading=13, textColor=colors.white),
    "toc": ParagraphStyle("toc", fontName="Helvetica", fontSize=11, leading=17,
                          textColor=INK),
}


# standard Type1 fonts are WinAnsi-only: map box-drawing art to ASCII
BOX_TO_ASCII = str.maketrans({
    "┌": "+", "┐": "+", "└": "+", "┘": "+", "├": "+", "┤": "+",
    "┬": "+", "┴": "+", "┼": "+", "─": "-", "│": "|",
    "►": ">", "◄": "<", "▲": "^", "▼": "v",
})


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def inline(text: str) -> str:
    """Markdown inline formatting -> reportlab XML markup."""
    text = esc(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"`([^`]+)`", r'<font face="Courier" size="8.6">\1</font>', text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # links -> text
    return text


def make_table(rows: list[list[str]], width: float) -> Table:
    data = [[Paragraph(inline(c), STYLES["cellhead"]) for c in rows[0]]]
    for row in rows[1:]:
        data.append([Paragraph(inline(c), STYLES["cell"]) for c in row])
    table = Table(data, colWidths=[width / len(rows[0])] * len(rows[0]))
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CODE_BG]),
    ]))
    return table


def parse(md: str, text_width: float):
    story = []
    lines = md.splitlines()
    i = 0
    para: list[str] = []

    def flush_para():
        if para:
            story.append(Paragraph(inline(" ".join(para)), STYLES["body"]))
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_para()
            block = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            # a diagram split down the middle is unreadable; keep it whole
            # (KeepTogether degrades to a normal split if it exceeds a page)
            story.append(KeepTogether(XPreformatted(
                esc("\n".join(block).translate(BOX_TO_ASCII)), STYLES["code"])))
        elif stripped == "{{CONTENTS}}":
            flush_para()
            toc = TableOfContents()
            toc.levelStyles = [STYLES["toc"]]
            toc.dotsMinLevel = 0
            story.append(toc)
        elif stripped.startswith("# ") and not story:
            story.append(Paragraph(inline(stripped[2:]), STYLES["title"]))
        elif stripped.startswith("## "):
            flush_para()
            story.append(CondPageBreak(SECTION_HEADROOM))
            story.append(Paragraph(inline(stripped[3:]), STYLES["h1"]))
        elif stripped.startswith("|"):
            flush_para()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            i -= 1
            if rows:
                # a table cut across a page loses its header row and its
                # meaning; these are all small enough to keep whole
                story.append(KeepTogether([
                    Spacer(1, 3), make_table(rows, text_width), Spacer(1, 7),
                ]))
        elif re.match(r"^(-|\d+\.)\s+", stripped):
            flush_para()
            m = re.match(r"^(-|\d+\.)\s+(.*)$", stripped)
            marker = "•" if m.group(1) == "-" else m.group(1)
            body = [m.group(2)]
            while i + 1 < len(lines) and lines[i + 1].startswith("   ") and lines[i + 1].strip():
                i += 1
                body.append(lines[i].strip())
            story.append(Paragraph(inline(" ".join(body)), STYLES["bullet"],
                                   bulletText=marker))
        elif stripped == "---":
            flush_para()
            story.append(Spacer(1, 4))
            story.append(HRFlowable(width="100%", thickness=0.6, color=BORDER))
            story.append(Spacer(1, 6))
        elif stripped.startswith("**Version"):
            story.append(Paragraph(inline(stripped.strip("*")), STYLES["meta"]))
        elif not stripped:
            flush_para()
        else:
            para.append(stripped)
        i += 1
    flush_para()
    return story


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(DIM)
    canvas.drawString(MARGIN, 0.55 * inch, "Syrudas AI — Whitepaper")
    canvas.drawRightString(letter[0] - MARGIN, 0.55 * inch, f"Page {doc.page}")
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, 0.72 * inch, letter[0] - MARGIN, 0.72 * inch)
    canvas.restoreState()


class WhitepaperDoc(SimpleDocTemplate):
    """SimpleDocTemplate that reports its section headings to the contents page."""

    def afterFlowable(self, flowable) -> None:
        if isinstance(flowable, Paragraph) and flowable.style.name == "h1":
            text = flowable.getPlainText()
            if text != "Contents":  # a contents page listing itself is noise
                self.notify("TOCEntry", (0, text, self.page))


def main() -> int:
    doc = WhitepaperDoc(
        str(OUT), pagesize=letter,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=0.8 * inch, bottomMargin=0.95 * inch,
        title="Syrudas AI: A Local-First AI Workspace with Pluggable Model Providers",
        author="Len",
    )
    text_width = letter[0] - 2 * MARGIN
    source = SRC.read_text(encoding="utf-8").replace("{{VERSION}}", APP_VERSION)
    story = parse(source, text_width)
    # multiBuild: the contents page needs page numbers that only exist after a
    # first pass, so the document is laid out repeatedly until they settle
    doc.multiBuild(story, onFirstPage=footer, onLaterPages=footer)
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
