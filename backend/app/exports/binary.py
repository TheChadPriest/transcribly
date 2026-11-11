"""DOCX (python-docx) and PDF (ReportLab) renderers for the study document."""

from __future__ import annotations

import io
import os
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

from .document import Block

# Unicode TTF candidates; ReportLab's built-in fonts only cover Latin-1.
FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/dejavu/DejaVuSans-Oblique.ttf"),
    (os.path.expandvars(r"%WINDIR%\Fonts\arial.ttf"), os.path.expandvars(r"%WINDIR%\Fonts\arialbd.ttf"),
     os.path.expandvars(r"%WINDIR%\Fonts\ariali.ttf")),
    ("/Library/Fonts/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf"),
]


def to_docx(blocks: list[Block]) -> bytes:
    from docx import Document
    from docx.shared import Pt, RGBColor

    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    for b in blocks:
        if b.kind == "h1":
            doc.add_heading(b.text, level=0)
        elif b.kind == "h2":
            doc.add_heading(b.text, level=1)
        elif b.kind == "h3":
            doc.add_heading(b.text, level=2)
        elif b.kind == "bullet":
            doc.add_paragraph(b.text, style="List Bullet")
        elif b.kind == "meta":
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(0)
            p.add_run(b.text)
        elif b.kind == "transcript":
            p = doc.add_paragraph()
            if b.timestamp:
                ts = p.add_run(f"[{b.timestamp}] ")
                ts.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
            p.add_run(b.text)
        else:
            p = doc.add_paragraph()
            for r in b.runs:
                run = p.add_run(r.text)
                run.italic = r.italic
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@lru_cache
def _pdf_fonts() -> tuple[str, str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for regular, bold, italic in FONT_CANDIDATES:
        if Path(regular).exists():
            try:
                pdfmetrics.registerFont(TTFont("TB", regular))
                pdfmetrics.registerFont(TTFont("TB-Bold", bold if Path(bold).exists() else regular))
                pdfmetrics.registerFont(TTFont("TB-Italic", italic if Path(italic).exists() else regular))
                from reportlab.lib.fonts import addMapping

                addMapping("TB", 0, 0, "TB")
                addMapping("TB", 1, 0, "TB-Bold")
                addMapping("TB", 0, 1, "TB-Italic")
                addMapping("TB", 1, 1, "TB-Bold")
                return "TB", "TB-Bold", "TB-Italic"
            except Exception:
                continue
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"


def to_pdf(blocks: list[Block], title: str) -> bytes:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        ListFlowable,
        ListItem,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
    )

    regular, bold, _italic = _pdf_fonts()
    base = ParagraphStyle("base", fontName=regular, fontSize=10.5, leading=15, spaceAfter=6)
    styles = {
        "h1": ParagraphStyle("h1", parent=base, fontName=bold, fontSize=18, leading=23, spaceAfter=10),
        "h2": ParagraphStyle("h2", parent=base, fontName=bold, fontSize=13.5, leading=18, spaceBefore=12, spaceAfter=6),
        "h3": ParagraphStyle("h3", parent=base, fontName=bold, fontSize=11.5, leading=15, spaceBefore=8, spaceAfter=3),
        "meta": ParagraphStyle("meta", parent=base, textColor=HexColor("#555555"), spaceAfter=1),
        "p": base,
        "transcript": base,
    }
    ts_color = "#777777"

    def markup(b: Block) -> str:
        out = []
        for r in b.runs:
            t = escape(r.text)
            out.append(f"<i>{t}</i>" if r.italic else t)
        return "".join(out)

    story: list = []
    bullets: list = []

    def flush_bullets() -> None:
        if bullets:
            story.append(ListFlowable([ListItem(p, leftIndent=12) for p in bullets], bulletType="bullet",
                                      start="•", leftIndent=12, bulletFontName=regular))
            bullets.clear()

    for b in blocks:
        if b.kind == "bullet":
            bullets.append(Paragraph(markup(b), base))
            continue
        flush_bullets()
        if b.kind == "transcript" and b.timestamp:
            story.append(Paragraph(f'<font color="{ts_color}">[{b.timestamp}]</font> {markup(b)}', base))
        else:
            story.append(Paragraph(markup(b), styles.get(b.kind, base)))
        if b.kind == "h1":
            story.append(Spacer(1, 2 * mm))
    flush_bullets()

    buf = io.BytesIO()

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColor(HexColor("#888888"))
        canvas.drawString(18 * mm, 10 * mm, "Transcribly")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, str(doc.page))
        canvas.restoreState()

    SimpleDocTemplate(buf, pagesize=A4, title=title, author="", creator="Transcribly",
                      leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm
                      ).build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def pdf_unicode_supported() -> bool:
    return _pdf_fonts()[0] != "Helvetica"
