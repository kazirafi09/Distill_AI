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
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle

    with _font_lock:
        if "Distill" not in pdfmetrics.getRegisteredFontNames():
            fonts = Path(reportlab.__file__).parent / "fonts"
            choices = [(Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'), Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')),
                (Path('C:/Windows/Fonts/segoeui.ttf'), Path('C:/Windows/Fonts/segoeuib.ttf')),
                (Path('C:/Windows/Fonts/arial.ttf'), Path('C:/Windows/Fonts/arialbd.ttf')),
                (fonts / 'Vera.ttf', fonts / 'VeraBd.ttf')]
            regular, bold = next(pair for pair in choices if all(path.is_file() for path in pair))
            pdfmetrics.registerFont(TTFont("Distill", str(regular)))
            pdfmetrics.registerFont(TTFont("DistillBold", str(bold)))
    styles = {
        "text": ParagraphStyle("body", fontName="Distill", fontSize=9, leading=14, textColor=colors.HexColor("#243447"), spaceAfter=8, alignment=TA_LEFT, splitLongWords=True),
        "heading": ParagraphStyle("heading", fontName="DistillBold", fontSize=10, leading=15, textColor=colors.HexColor("#065f46"), spaceBefore=10, spaceAfter=6, keepWithNext=True, splitLongWords=True),
        "note": ParagraphStyle("note", fontName="Distill", fontSize=7.5, leading=11, textColor=colors.HexColor("#536477"), spaceAfter=6, splitLongWords=True),
        "quote": ParagraphStyle("quote", fontName="Distill", fontSize=9, leading=14, textColor=colors.HexColor("#334155"), leftIndent=12, borderPadding=7, backColor=colors.HexColor("#f1f8f5"), spaceAfter=9, splitLongWords=True),
        "warning": ParagraphStyle("warning", fontName="Distill", fontSize=8.5, leading=13, textColor=colors.HexColor("#854d0e"), borderPadding=7, backColor=colors.HexColor("#fffbeb"), spaceBefore=5, spaceAfter=12, splitLongWords=True),
    }
    compact = document.get("report_type") == "recruiter"
    if compact:
        styles['text'].fontSize, styles['text'].leading, styles['text'].spaceAfter = 8.5, 11, 4
        styles['heading'].fontSize, styles['heading'].leading = 9, 12
        styles['heading'].spaceBefore, styles['heading'].spaceAfter = 4, 3
        styles['note'].fontSize, styles['note'].leading, styles['note'].spaceAfter = 7, 9, 4
        styles['warning'].fontSize, styles['warning'].leading = 8, 10
        styles['warning'].borderPadding = 3
        styles['warning'].spaceBefore, styles['warning'].spaceAfter = 8, 8
    section_style = ParagraphStyle("section", parent=styles["heading"], fontSize=17, leading=23, spaceAfter=18)
    title_style = ParagraphStyle("title", parent=section_style, fontSize=23, leading=30)
    if compact:
        section_style.fontSize, section_style.leading, section_style.spaceAfter = 11, 14, 6
        title_style.fontSize, title_style.leading, title_style.spaceAfter = 18, 22, 5
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
    if compact:
        from app.services.interviews.full_report import moment
        meta = document['metadata']
        paragraph(f"{meta['job']['title']} | JD {meta['job']['number']} | Generated {moment(meta['generated_at'])}", styles['note'])
        paragraph(('DEMO - Synthetic sample. ' if meta['run']['synthetic'] else '') + ('Historical report. ' if meta.get('outdated') else '') + 'For recruiter review. Not a hiring decision.', styles['note'])
    for index, section in enumerate(document["sections"]):
        if index and (section.get("page_break_before") or section.get("id") == "evidence-appendix"):
            story.append(PageBreak())
        else:
            story.append(Spacer(1, (6 if index else 4) if compact else (14 if index else 12)))
        paragraph(section["title"], section_style)
        if section.get("table"):
            table = section["table"]
            cell = ParagraphStyle("table-cell", parent=styles["note"], fontSize=7, leading=10, spaceAfter=0)
            matrix = [[Paragraph(escape(str(value)), cell) for value in row]
                for row in [table["columns"], *table["rows"]]]
            widths = table.get('widths') or [.38, .14, .16, .17, .15]
            grid = Table(matrix, colWidths=[pdf.width * part for part in widths],
                repeatRows=1, hAlign="LEFT", splitByRow=1, splitInRow=1)
            grid.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f5")),
                ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
            story.extend([grid, Spacer(1, 5 if compact else 12)])
        for block in section["blocks"]:
            refs = block.get('citation_ids', [])
            paragraph(block["text"] + (f" [{', '.join(refs)}]" if refs else ''), styles[block["kind"]])

    def frame(canvas, doc):
        canvas.saveState()
        width, height = A4
        canvas.setStrokeColor(colors.HexColor("#dce7e2"))
        canvas.line(46, height - 39, width - 46, height - 39)
        canvas.setFont("DistillBold", 8)
        canvas.setFillColor(colors.HexColor("#047857"))
        canvas.drawString(46, height - 30, "DISTILL AI  /  RECRUITER REPORT" if compact else "DISTILL AI  /  FULL ASSESSMENT")
        canvas.setFont("Distill", 7)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(46, 29, "Confidential | Synthetic sample" if document["metadata"]["run"]["synthetic"] else "Confidential | Recruiter review")
        canvas.drawRightString(width - 46, 29, f"Page {doc.page}")
        run = document["metadata"]["run"]
        history = "Historical | " if document["metadata"].get("outdated") else ""
        canvas.setFont("Distill", 6)
        versions = document["metadata"].get("source_versions", {})
        rubric = versions.get("interview_rubric") or (run.get("rubric_version") if run.get("kind") == "f5" else "Not recorded")
        version_label = f"Transcript v{versions.get('transcript') or '?'}" if compact else f"Interview rubric {rubric}"
        canvas.drawString(46, 17, f"{history}Not a hiring decision | Criteria v{run.get('profile_version', '?')} | {version_label}")
        canvas.restoreState()
    pdf.build(story, onFirstPage=frame, onLaterPages=frame)
    return stream.getvalue()
