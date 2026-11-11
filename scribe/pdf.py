"""In-memory PDF of the (clinician-edited) SOAP note. Nothing is written to disk."""
from __future__ import annotations

import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .schema import ExportRequest

INK = colors.HexColor("#14232f")
MUTED = colors.HexColor("#5b6b78")
ACCENT = colors.HexColor("#0f6f73")
RULE = colors.HexColor("#d8e1e6")

LABELS = [("subjective", "S", "Subjective"), ("objective", "O", "Objective"),
          ("assessment", "A", "Assessment"), ("plan", "P", "Plan")]


def build_pdf(req: ExportRequest) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm, title="LinguaLink360 SOAP Note")
    ss = getSampleStyleSheet()
    title = ParagraphStyle("t", parent=ss["Title"], fontSize=17, leading=21, textColor=INK, alignment=0, spaceAfter=2)
    sub = ParagraphStyle("s", parent=ss["Normal"], fontSize=8.5, textColor=MUTED, leading=12)
    head = ParagraphStyle("h", parent=ss["Heading3"], fontSize=11, textColor=ACCENT, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9.6, leading=14, textColor=INK, spaceAfter=3)
    small = ParagraphStyle("sm", parent=body, fontSize=8.3, textColor=MUTED, leading=11)

    story = [Paragraph("LinguaLink360 · Session SOAP Note", title),
             Paragraph("AI-assisted draft. Reviewed and edited by the treating clinician.", sub), Spacer(1, 6)]

    h = {k: v for k, v in req.header.items() if v}
    if h:
        pairs = [(k.replace("_", " ").title(), escape(v)) for k, v in h.items()]
        rows = [[Paragraph(f"<b>{a}</b>", small), Paragraph(b, small)] for a, b in pairs]
        t = Table(rows, colWidths=[38 * mm, None])
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        story += [t, Spacer(1, 4)]

    any_proposed = False
    for key, letter, label in LABELS:
        items = [s for s in req.soap.get(key) or [] if s.text.strip() and not s.pending]
        parts = [Paragraph(f"{letter} · {label}", head)]
        texts = []
        for s in items:
            mark = " †" if s.origin == "ai_proposed" else ""
            any_proposed |= bool(mark)
            texts.append(escape(s.text.strip()).replace("\n", "<br/>") + mark)
        parts += [Paragraph(x, body) for x in texts or ["Not documented."]]
        story.append(KeepTogether(parts))
    if any_proposed:
        story.append(Paragraph("† Proposed by the AI assistant and accepted by the treating clinician.", small))

    if req.summary.strip():
        story += [Paragraph("Clinical analysis summary", head), Paragraph(escape(req.summary.strip()), small)]

    story.append(Spacer(1, 14))
    if req.signed_by:
        story.append(Paragraph(f"<b>Reviewed and signed by:</b> {escape(req.signed_by)}"
                               f"{' · ' + escape(req.signed_at) if req.signed_at else ''}", body))
    else:
        story.append(Paragraph("<b>Status:</b> Unsigned draft. Clinician review required before use.", body))
    doc.build(story)
    return buf.getvalue()
