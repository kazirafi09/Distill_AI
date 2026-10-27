"""Extraction cache identity and validation; no graph construction or execution."""
from copy import deepcopy

from app.services.cv.text import readable
from app.services.wind_tunnel import storage

EXTRACTION_KEYS = ("extractor", "ocr", "ocr_language_data", "tesseract", "poppler", "pypdf",
                   "python_docx", "pillow", "ocr_language", "ocr_page_timeout", "document_timeout")


def extraction_config(versions):
    if not set(EXTRACTION_KEYS) <= set(versions):
        raise ValueError("SYNTHETIC_EXTRACTION_CONFIG_INVALID")
    return {key: versions[key] for key in EXTRACTION_KEYS}


def validate_extraction(entry, document, versions):
    if (entry.get("status") != "completed" or entry.get("file_sha256") != document.file_sha256
            or entry.get("document_id") != str(document.id) or entry.get("config") != extraction_config(versions)):
        raise ValueError("SYNTHETIC_EXTRACTION_NOT_REUSABLE")
    body = {key: entry[key] for key in ("text", "sources", "warnings", "extraction_method")}
    if storage.digest(body) != entry.get("content_digest") or not body["sources"]:
        raise ValueError("SYNTHETIC_EXTRACTION_INTEGRITY_FAILED")
    ids, previous = set(), 0
    for source in body["sources"]:
        if (source["id"] in ids or source["method"] not in {"native", "ocr"}
                or source.get("ocr_failed") or not previous <= source["start"] <= source["end"] <= len(body["text"])):
            raise ValueError("SYNTHETIC_EXTRACTION_INTEGRITY_FAILED")
        ids.add(source["id"])
        previous = source["end"]
    if not any(readable(body["text"][s["start"]:s["end"]]) for s in body["sources"]):
        raise ValueError("SYNTHETIC_EXTRACTION_NOT_REUSABLE")
    return body


def apply_extraction(parsed, entry, document, versions):
    body = validate_extraction(entry, document, versions)
    if parsed.text is not None:
        if parsed.text != body["text"] or parsed.sources != body["sources"]:
            raise ValueError("SYNTHETIC_EXTRACTION_INTEGRITY_FAILED")
        return
    # Preserve fresh parse identity, profile, matches and status for each pass.
    for key, value in deepcopy(body).items():
        setattr(parsed, key, value)
