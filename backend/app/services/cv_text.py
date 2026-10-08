"""Bounded, local extraction. Child processes isolate corrupt documents and OCR.

Short readable text is usable. OCR requires an image plus insufficient text:
unusable text, or <40 words and a raster with >=250,000 pixels. A page's OCR
replaces its native text (never concatenates both). Partial failures require review.
"""
import json
import re
import subprocess
import sys
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from PIL import Image
from pypdf import PdfReader
from pypdf.generic import ContentStream

from app.services.document_extraction import (
    DocumentExtractionError, MAX_FILE_BYTES, MAX_PDF_PAGES, MAX_TEXT_CHARACTERS,
    extract_docx, extract_pdf_page,
)

MAX_IMAGE_PIXELS = 4_000_000
MAX_EMBEDDED_IMAGES = 30
Image.MAX_IMAGE_PIXELS = 20_000_000


def readable(text: str) -> bool:
    text = text.strip()
    if not text or "\ufffd" in text and text.count("\ufffd") / len(text) > .02:
        return False
    printable = sum(c.isprintable() or c in "\n\t" for c in text) / len(text)
    words = re.findall(r"[^\W_]+", text, re.UNICODE)
    if not words or max((len(w) for w in words), default=0) > 200:
        return False
    if len(words) > 10 and sum(len(w) == 1 for w in words) / len(words) > .7:
        return False
    # PDF layout extraction can contain long runs of spaces or rule characters.
    # Only repeated letters/digits indicate this particular corruption signal.
    if re.search(r"([^\W_])\1{15,}", text):
        return False
    return printable >= .95 and any(len(w) >= 2 and any(c.isalpha() for c in w) for w in words)


def needs_ocr(text: str, has_image: bool, large_image: bool) -> bool:
    return has_image and (not readable(text) or (large_image and len(text.split()) < 40))


def pdf_image_coverage(page) -> tuple[bool, bool]:
    """Inspect drawing transforms rather than treating a high-DPI logo as a scan.

    OCR readable short pages only when substantial raster coverage (>=15%) and
    >=250k source pixels coexist. Recurse through forms to find nested images.
    """
    image_pixels, image_area = [], 0.0
    page_area = max(1., float(page.mediabox.width * page.mediabox.height))

    def walk(stream, resources, scale=1., depth=0):
        nonlocal image_area
        if depth > 5:
            return
        resources = resources.get_object() if hasattr(resources, "get_object") else resources
        objects = resources.get("/XObject", {})
        objects = objects.get_object() if hasattr(objects, "get_object") else objects
        operations = stream.operations if isinstance(stream, ContentStream) else ContentStream(stream, page.pdf).operations
        stack = []
        for args, operator in operations:
            if operator == b"q":
                stack.append(scale)
            elif operator == b"Q" and stack:
                scale = stack.pop()
            elif operator == b"cm":
                a, b, c, d = (float(v) for v in args[:4])
                scale *= abs(a * d - b * c)
            elif operator == b"Do" and args and args[0] in objects:
                obj = objects[args[0]].get_object()
                if obj.get("/Subtype") == "/Image":
                    image_pixels.append(int(obj.get("/Width", 0)) * int(obj.get("/Height", 0)))
                    image_area += scale
                elif obj.get("/Subtype") == "/Form":
                    matrix = obj.get("/Matrix", [1, 0, 0, 1, 0, 0])
                    factor = abs(float(matrix[0]) * float(matrix[3]) - float(matrix[1]) * float(matrix[2]))
                    walk(obj, obj.get("/Resources", resources), scale * factor, depth + 1)
            elif operator == b"INLINE IMAGE":
                image_pixels.append(250_000)
                image_area += scale
    contents = page.get_contents()
    if contents is not None:
        walk(contents, page.get("/Resources", {}))
    return bool(image_pixels), image_area / page_area >= .15 and any(size >= 250_000 for size in image_pixels)


def run_ocr(image_path: Path, language: str, timeout: int) -> str:
    # Both rasterization and OCR have individual timeouts; the parent additionally
    # kills the entire process group at the document deadline.
    with Image.open(image_path) as image:
        if image.width * image.height > MAX_IMAGE_PIXELS:
            ratio = (MAX_IMAGE_PIXELS / (image.width * image.height)) ** .5
            image = image.resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))))
        image.convert("RGB").save(image_path.with_suffix(".png"))
    completed = subprocess.run(
        ["tesseract", str(image_path.with_suffix(".png")), "stdout", "-l", language, "--psm", "3"],
        capture_output=True, timeout=timeout, check=True,
    )
    if len(completed.stdout) > MAX_TEXT_CHARACTERS * 4:
        raise ValueError("OCR_OUTPUT_TOO_LARGE")
    return completed.stdout.decode("utf-8", errors="replace").replace("\x00", "").strip()


def extract_native(path: Path) -> dict:
    data = path.read_bytes()
    if not 0 < len(data) <= MAX_FILE_BYTES:
        raise DocumentExtractionError("INVALID_SIZE", "Invalid size")
    sources, warnings = [], []
    if path.suffix.lower() == ".pdf":
        if b"%PDF-" not in data[:1024]:
            raise DocumentExtractionError("INVALID_FILE", "Invalid PDF")
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise DocumentExtractionError("ENCRYPTED_PDF", "Encrypted PDF")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise DocumentExtractionError("TOO_MANY_PAGES", "Too many pages")
        for number, page in enumerate(reader.pages, 1):
            warning = None
            try:
                text = extract_pdf_page(page).replace("\x00", "").strip()
            except Exception:
                text, warning = "", f"PDF_PAGE_{number}_EXTRACTION_FAILED"
            try:
                has_image, large_image = pdf_image_coverage(page)
            except Exception:
                has_image, large_image = True, False
                warnings.append(f"PDF_PAGE_{number}_IMAGE_LAYOUT_UNKNOWN")
            sources.append({"id": f"pdf-page-{number}", "page": number, "method": "native", "text": text,
                            "ocr_needed": needs_ocr(text, has_image, large_image)})
            if warning:
                warnings.append(warning)
    elif path.suffix.lower() == ".docx":
        text = extract_docx(data).replace("\x00", "").strip()
        sources.append({"id": "docx-body", "page": None, "method": "native", "text": text, "ocr_needed": False})
        with ZipFile(BytesIO(data)) as archive:
            images = sorted(name for name in archive.namelist() if name.startswith("word/media/"))
            if len(images) > MAX_EMBEDDED_IMAGES:
                raise DocumentExtractionError("TOO_MANY_IMAGES", "Too many embedded images")
            for number, name in enumerate(images, 1):
                # Large embedded images may contain CV sections even in readable DOCX.
                try:
                    with Image.open(BytesIO(archive.read(name))) as image:
                        large = image.width * image.height >= 250_000
                    if large or not readable(text):
                        sources.append({"id": f"docx-image-{number}", "page": None, "method": "ocr", "text": "",
                                        "ocr_needed": True, "archive_name": name})
                except Exception:
                    warnings.append(f"DOCX_IMAGE_{number}_UNSUPPORTED")
            if images:
                warnings.append("DOCX_IMAGES_APPENDED_AFTER_BODY_ORDER_UNKNOWN")
            warnings.append("DOCX_HEADERS_FOOTERS_TEXTBOXES_DRAWINGS_NOT_EXTRACTED")
    else:
        raise DocumentExtractionError("UNSUPPORTED_FILE_TYPE", "Unsupported file")
    if sum(len(s["text"]) for s in sources) > MAX_TEXT_CHARACTERS:
        raise DocumentExtractionError("TOO_MUCH_TEXT", "Too much text")
    return {"sources": sources, "warnings": warnings}


def ocr_sources(path: Path, extracted: dict, language: str, timeout: int) -> dict:
    with TemporaryDirectory(prefix="distill-ocr-") as directory:
        root = Path(directory)
        for source in extracted["sources"]:
            if not source.get("ocr_needed"):
                continue
            try:
                image_path = root / "page.png"
                if source["page"] is not None:
                    number = str(source["page"])
                    subprocess.run(["pdftoppm", "-f", number, "-l", number, "-singlefile", "-scale-to", "2000", "-png",
                                    str(path), str(root / "page")], capture_output=True, timeout=timeout, check=True)
                else:
                    with ZipFile(path) as archive:
                        image_path.write_bytes(archive.read(source["archive_name"]))
                text = run_ocr(image_path, language, timeout)
                if not readable(text):
                    raise ValueError("OCR_UNREADABLE")
                source.update(text=text, method="ocr", ocr_needed=False)
            except Exception as exc:
                extracted["warnings"].append(f"{source['id']}:OCR_{'TIMEOUT' if isinstance(exc, subprocess.TimeoutExpired) else 'FAILED'}")
                source["ocr_failed"] = True
            if sum(len(s["text"]) for s in extracted["sources"]) > MAX_TEXT_CHARACTERS:
                raise DocumentExtractionError("TOO_MUCH_TEXT", "Too much text")
    return extracted


def finalize_text(extracted: dict) -> dict:
    text, sources = "", []
    for source in extracted["sources"]:
        source = {k: v for k, v in source.items() if k not in {"archive_name", "ocr_needed"}}
        marker = f"[{source['id']} | {source['method']}]\n"
        text += marker
        source["start"] = len(text)
        text += source.pop("text") + "\n\n"
        source["end"] = len(text) - 2
        sources.append(source)
    if len(text) > MAX_TEXT_CHARACTERS:
        raise DocumentExtractionError("TOO_MUCH_TEXT", "Too much text")
    usable = any(readable(text[s["start"]:s["end"]]) for s in sources)
    incomplete = any(s.get("ocr_failed") or (s.get("page") and not readable(text[s["start"]:s["end"]])) for s in sources)
    methods = {s["method"] for s in sources if s["end"] > s["start"]}
    return {"text": text, "sources": sources, "warnings": extracted["warnings"],
            "extraction_method": "mixed" if len(methods) > 1 else next(iter(methods), "native"),
            "usable": usable and not incomplete}


def isolated_extract(path: Path, mode: str, language: str, page_timeout: int, document_timeout: int,
                     extracted: dict | None = None, expected_sha256: str | None = None) -> dict:
    """Use a subprocess rather than a timed-out thread that keeps consuming CPU."""
    import os
    import signal
    with TemporaryDirectory(prefix="distill-extract-") as directory:
        input_path, output_path = Path(directory) / "input.json", Path(directory) / "output.json"
        # Process an immutable bounded snapshot. Recheck its hash so replacing a
        # stored file between graph nodes cannot mix two different CV versions.
        with path.open("rb") as source_file:
            data = source_file.read(MAX_FILE_BYTES + 1)
        if not 0 < len(data) <= MAX_FILE_BYTES:
            raise DocumentExtractionError("INVALID_SIZE", "Invalid size")
        if expected_sha256 and sha256(data).hexdigest() != expected_sha256:
            raise DocumentExtractionError("FILE_HASH_MISMATCH", "CV hash changed")
        snapshot_path = Path(directory) / ("source" + path.suffix.lower())
        snapshot_path.write_bytes(data)
        input_path.write_text(json.dumps(extracted), encoding="utf-8")
        process = subprocess.Popen([sys.executable, "-m", "app.services.cv_extract_worker", str(snapshot_path), mode,
                                    str(input_path), str(output_path), language, str(page_timeout), str(document_timeout), str(os.getpid())],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=os.name != "nt")
        try:
            process.wait(timeout=document_timeout)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()
            raise DocumentExtractionError("DOCUMENT_TIMEOUT", "Document processing timed out") from None
        if not output_path.is_file() or output_path.stat().st_size > 4_000_000:
            raise DocumentExtractionError("EXTRACTION_FAILED", "Extraction failed")
        result = json.loads(output_path.read_text(encoding="utf-8"))
        if "error" in result:
            raise DocumentExtractionError(result["error"], "Document extraction failed")
        return result
