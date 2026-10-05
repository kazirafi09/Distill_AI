from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from docx import Document
from docx.text.paragraph import Paragraph
from pypdf import PdfReader


MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_DOCX_EXPANDED_BYTES = 30 * 1024 * 1024
MAX_TEXT_CHARACTERS = 500_000


class DocumentExtractionError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ExtractedDocument:
    filename: str
    file_type: str
    file_size_bytes: int
    file_sha256: str
    text: str


def extract_pdf(data: bytes) -> str:
    if b"%PDF-" not in data[:1024]:
        raise DocumentExtractionError(
            "INVALID_FILE",
            "The file is not a valid PDF.",
        )

    reader = PdfReader(BytesIO(data))

    if reader.is_encrypted:
        raise DocumentExtractionError(
            "ENCRYPTED_PDF",
            "Password-protected PDFs are not supported.",
        )

    if len(reader.pages) > MAX_PDF_PAGES:
        raise DocumentExtractionError(
            "TOO_MANY_PAGES",
            f"PDFs must contain at most {MAX_PDF_PAGES} pages.",
        )

    parts = []
    text_length = 0

    for page in reader.pages:
        contents = page.get_contents()

        if contents is None:
            parts.append("")
            continue

        if len(contents.get_data()) > MAX_FILE_BYTES:
            raise DocumentExtractionError(
                "DOCUMENT_TOO_COMPLEX",
                "A PDF page is too large to process.",
            )

        text = page.extract_text(
            extraction_mode="layout",
            layout_mode_space_vertically=False,
        ) or ""

        text_length += len(text)

        if text_length > MAX_TEXT_CHARACTERS:
            raise DocumentExtractionError(
                "TOO_MUCH_TEXT",
                "The document contains too much text.",
            )

        parts.append(text)

    return "\n\n".join(parts)


def extract_docx(data: bytes) -> str:
    with ZipFile(BytesIO(data)) as archive:
        entries = archive.infolist()

        if (
            len(entries) > 1000
            or sum(entry.file_size for entry in entries)
            > MAX_DOCX_EXPANDED_BYTES
        ):
            raise DocumentExtractionError(
                "DOCUMENT_TOO_COMPLEX",
                "The DOCX contents are too large to process.",
            )

        if "word/document.xml" not in archive.namelist():
            raise DocumentExtractionError(
                "INVALID_FILE",
                "The file is not a valid DOCX document.",
            )

    document = Document(BytesIO(data))
    parts = []

    # Preserve the order of body paragraphs and tables.
    for block in document.iter_inner_content():
        if isinstance(block, Paragraph):
            parts.append(block.text)
        else:
            for row in block.rows:
                parts.append(
                    " | ".join(cell.text for cell in row.cells)
                )

    return "\n\n".join(parts)


def extract_document(filename: str, data: bytes) -> ExtractedDocument:
    safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if (
        not safe_name
        or len(safe_name) > 255
        or any(ord(character) < 32 for character in safe_name)
    ):
        raise DocumentExtractionError(
            "INVALID_FILENAME",
            "Provide a valid filename of at most 255 characters.",
        )

    suffix = Path(safe_name).suffix.lower()

    if suffix not in {".pdf", ".docx"}:
        raise DocumentExtractionError(
            "UNSUPPORTED_FILE_TYPE",
            "Only PDF and DOCX files are supported.",
        )

    if not data:
        raise DocumentExtractionError(
            "EMPTY_FILE",
            "The uploaded file is empty.",
        )

    if len(data) > MAX_FILE_BYTES:
        raise DocumentExtractionError(
            "FILE_TOO_LARGE",
            "Files must be at most 10 MB.",
        )

    try:
        text = (
            extract_pdf(data)
            if suffix == ".pdf"
            else extract_docx(data)
        )
    except DocumentExtractionError:
        raise
    except Exception as exc:
        raise DocumentExtractionError(
            "INVALID_DOCUMENT",
            "The document is corrupted or could not be read.",
        ) from exc

    text = text.replace("\x00", "").strip()

    if not text:
        raise DocumentExtractionError(
            "NO_READABLE_TEXT",
            "No readable text was found. The file may be blank or require OCR.",
        )

    if len(text) > MAX_TEXT_CHARACTERS:
        raise DocumentExtractionError(
            "TOO_MUCH_TEXT",
            "The document contains too much text.",
        )

    return ExtractedDocument(
        filename=safe_name,
        file_type=suffix[1:],
        file_size_bytes=len(data),
        file_sha256=sha256(data).hexdigest(),
        text=text,
    )