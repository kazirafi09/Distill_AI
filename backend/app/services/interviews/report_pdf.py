"""Paginated PDF from the same report document shown in the UI."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
import threading

_font_lock = threading.Lock()


def render(document):
    import reportlab
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak

    with _font_lock:
        if "Distill" not in pdfmetrics.getRegisteredFontNames():
            fonts = Path(reportlab.__file__).parent / "fonts"
            pdfmetrics.registerFont(TTFont("Distill", str(fonts / "Vera.ttf")))
            pdfmetrics.registerFont(TTFont("DistillBold", str(fonts / "VeraBd.ttf")))
    styles = {
        "text": ParagraphStyle("body", fontName="Distill", fontSize=9, leading=14, textColor=colors.HexColor("#243447"), spaceAfter=8, alignment=TA_LEFT, splitLongWords=True),
        "heading": ParagraphStyle("heading", fontName="DistillBold", fontSize=10, leading=15, textColor=colors.HexColor("#065f46"), spaceBefore=10, spaceAfter=6, keepWithNext=True, splitLongWords=True),
        "note": ParagraphStyle("note", fontName="Distill", fontSize=7.5, leading=11, textColor=colors.HexColor("#536477"), spaceAfter=6, splitLongWords=True),
        "quote": ParagraphStyle("quote", fontName="Distill", fontSize=9, leading=14, textColor=colors.HexColor("#334155"), leftIndent=12, borderPadding=7, backColor=colors.HexColor("#f1f8f5"), spaceAfter=9, splitLongWords=True),
        "warning": ParagraphStyle("warning", fontName="Distill", fontSize=8.5, leading=13, textColor=colors.HexColor("#854d0e"), borderPadding=7, backColor=colors.HexColor("#fffbeb"), spaceBefore=5, spaceAfter=12, splitLongWords=True),
    }
    section_style = ParagraphStyle("section", parent=styles["heading"], fontSize=17, leading=23, spaceAfter=18)
    title_style = ParagraphStyle("title", parent=section_style, fontSize=23, leading=30)
    stream = BytesIO()
    pdf = SimpleDocTemplate(stream, pagesize=A4, rightMargin=46, leftMargin=46, topMargin=60, bottomMargin=50,
        title=document["title"], author="Distill AI", subject="Confidential assessment with full interview flow")
    story = []
    # Avoid markup injection: model/transcript/CV text is always literal text.
    def paragraph(text, style):
        # Split very large source paragraphs, without dropping any text, so they can paginate.
        for start in range(0, max(1, len(text)), 3000):
            story.append(Paragraph(escape(text[start:start + 3000]).replace("\n", "<br/>"), style))
    paragraph("DISTILL AI", styles["heading"])
    paragraph(document["title"], title_style)
    paragraph(document["metadata"]["candidate"]["name"], styles["heading"])
    for index, section in enumerate(document["sections"]):
        if index:
            story.append(PageBreak())
        else:
            story.append(Spacer(1, 12))
        paragraph(section["title"], section_style)
        for block in section["blocks"]:
            paragraph(block["text"], styles[block["kind"]])

    def frame(canvas, doc):
        canvas.saveState()
        width, height = A4
        canvas.setStrokeColor(colors.HexColor("#dce7e2"))
        canvas.line(46, height - 39, width - 46, height - 39)
        canvas.setFont("DistillBold", 8)
        canvas.setFillColor(colors.HexColor("#047857"))
        canvas.drawString(46, height - 30, "DISTILL AI  /  FULL ASSESSMENT")
        canvas.setFont("Distill", 7)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(46, 29, "Confidential | Synthetic sample" if document["metadata"]["run"]["synthetic"] else "Confidential | Recruiter review")
        canvas.drawRightString(width - 46, 29, f"Page {doc.page}")
        canvas.restoreState()
    pdf.build(story, onFirstPage=frame, onLaterPages=frame)
    return stream.getvalue()
