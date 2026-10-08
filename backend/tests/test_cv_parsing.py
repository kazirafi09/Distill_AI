"""Synthetic documents only; no candidate data or paid API calls."""
import asyncio
import json
import os
import subprocess
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from docx import Document
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.services.cv_profile_schema import identity_warnings, validate_profile
from app.services.cv_text import extract_native, finalize_text, isolated_extract, needs_ocr, ocr_sources, readable
from app.services.document_extraction import DocumentExtractionError, extract_document


def readable_pdf(words="Alice Example Python developer alice@example.com"):
    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 40 740 Td ({words}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = BytesIO(); writer.write(buffer)
    return buffer.getvalue()


def cv_image():
    image = Image.new("RGB", (1400, 1000), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 42)
    except OSError:
        font = ImageFont.load_default(size=42)
    for index, line in enumerate(["Alice Example", "alice@example.com", "Python developer", "Education: Bachelor of Science", "Experience: Example Labs 2022 - 2024"]):
        draw.text((70, 100 + index * 100), line, fill="black", font=font)
    return image


def claim(value, source="pdf-page-1"):
    return {"value": value, "evidence": [{"source_id": source, "quote": value}]}


class TextTests(unittest.TestCase):
    def test_short_readable_quality_and_jd_compatibility(self):
        self.assertTrue(readable("Python developer"))
        self.assertTrue(readable("Python" + " " * 60 + "developer"))
        self.assertFalse(readable("A" * 30 + " developer"))
        self.assertFalse(needs_ocr("Python developer", False, False))
        self.assertFalse(readable("\ufffd" * 100))
        self.assertTrue(needs_ocr("", True, True))
        data = readable_pdf()
        self.assertIn("Alice Example", extract_document("reference.pdf", data).text)
        self.assertNotIn("pdf-page", extract_document("reference.pdf", data).text)

    def test_readable_pdf_docx_markers_and_tables(self):
        with TemporaryDirectory() as root:
            path = Path(root) / "cv.pdf"; path.write_bytes(readable_pdf())
            native = extract_native(path)
            self.assertFalse(native["sources"][0]["ocr_needed"])
            final = finalize_text(native)
            self.assertTrue(final["usable"])
            self.assertEqual(final["sources"][0]["page"], 1)
            document = Document(); document.add_paragraph("Alice Example")
            document.add_table(rows=1, cols=2).rows[0].cells[0].text = "Python"
            path = Path(root) / "cv.docx"; document.save(path)
            final = finalize_text(extract_native(path))
            self.assertIn("Python", final["text"])
            self.assertIsNone(final["sources"][0]["page"])
            self.assertEqual(final["extraction_method"], "native")

    def test_encrypted_corrupt_and_blank(self):
        with TemporaryDirectory() as root:
            path = Path(root) / "cv.pdf"
            writer = PdfWriter(); writer.add_blank_page(100, 100); writer.encrypt("secret"); writer.write(path)
            with self.assertRaises(DocumentExtractionError) as failed:
                extract_native(path)
            self.assertEqual(failed.exception.code, "ENCRYPTED_PDF")
            path.write_bytes(b"corrupted")
            with self.assertRaises(DocumentExtractionError): extract_native(path)
            writer = PdfWriter(); writer.add_blank_page(100, 100); writer.write(path)
            self.assertFalse(finalize_text(extract_native(path))["usable"])

    def test_scanned_mixed_pdf_and_image_docx_real_local_ocr(self):
        with TemporaryDirectory() as root:
            root = Path(root)
            scanned = root / "scan.pdf"; cv_image().save(scanned, "PDF")
            native = extract_native(scanned)
            self.assertTrue(native["sources"][0]["ocr_needed"])
            extracted = isolated_extract(scanned, "ocr", "eng", 25, 120, native)
            final = finalize_text(extracted)
            self.assertTrue(final["usable"])
            self.assertIn("Alice Example", final["text"])
            self.assertEqual(final["extraction_method"], "ocr")
            writer = PdfWriter(); writer.add_page(PdfReader(BytesIO(readable_pdf("Native first page"))).pages[0]); writer.add_page(PdfReader(scanned).pages[0])
            mixed = root / "mixed.pdf"; writer.write(mixed)
            final = finalize_text(isolated_extract(mixed, "ocr", "eng", 25, 120, extract_native(mixed)))
            self.assertEqual([s["method"] for s in final["sources"]], ["native", "ocr"])
            self.assertEqual(final["text"].count("Alice Example"), 1)
            self.assertLess(final["text"].index("Native first page"), final["text"].index("Alice Example"))
            image = root / "cv.png"; cv_image().save(image)
            document = Document(); document.add_picture(str(image)); docx = root / "image.docx"; document.save(docx)
            final = finalize_text(isolated_extract(docx, "ocr", "eng", 25, 120, extract_native(docx)))
            self.assertTrue(final["usable"])
            self.assertIn("Alice Example", final["text"])
            self.assertTrue(all(s["page"] is None for s in final["sources"]))

    def test_ocr_timeout_and_failure_require_review(self):
        with TemporaryDirectory() as root:
            path = Path(root) / "scan.pdf"; cv_image().save(path, "PDF")
            for error in (subprocess.TimeoutExpired("ocr", 1), RuntimeError("ocr failed")):
                with patch("app.services.cv_text.subprocess.run", side_effect=error):
                    final = finalize_text(ocr_sources(path, extract_native(path), "eng", 1))
                self.assertFalse(final["usable"])
                self.assertTrue(final["warnings"])

    def test_document_deadline(self):
        with TemporaryDirectory() as root:
            path = Path(root) / "cv.pdf"; path.write_bytes(readable_pdf())
            with self.assertRaises(DocumentExtractionError) as failure:
                isolated_extract(path, "native", "eng", 1, .001)
            self.assertEqual(failure.exception.code, "DOCUMENT_TIMEOUT")


class SchemaTests(unittest.TestCase):
    def test_missing_identity_and_ocr_evidence(self):
        text = "Alice Example Python"
        sources = [{"id": "pdf-page-1", "start": 0, "end": len(text), "page": 1, "method": "ocr"}]
        profile, evidence = validate_profile({"contact": {"name": claim("Alice Example")}, "skills": [claim("Python")]}, text, sources)
        self.assertIsNone(profile["contact"]["email"])
        self.assertTrue(all(e["ocr_derived"] for e in evidence))
        warnings = identity_warnings(profile, "Other Name", "other@example.com")
        self.assertIn("CV_SHEET_NAME_DISCREPANCY", warnings)
        profile["contact"]["email"] = claim("alice@example.com")
        self.assertIn("CV_SHEET_EMAIL_DISCREPANCY", identity_warnings(profile, "Alice Example", "other@example.com"))
        with self.assertRaises(ValueError):
            validate_profile({"skills": [claim("Invented")]}, text, sources)
        bad = claim("10 years"); bad["evidence"][0]["quote"] = "Python"
        with self.assertRaises(ValueError): validate_profile({"skills": [bad]}, text, sources)
        with self.assertRaises(ValueError): validate_profile({}, text, sources)


class LLMTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider_patch = patch("app.services.cv_model.provider", return_value="ollama")
        self.provider_patch.start()
        self.addCleanup(self.provider_patch.stop)

    def test_layout_whitespace_restores_original_excerpts_without_accepting_paraphrases(self):
        from app.services.cv_profile_schema import resolve_excerpt
        source_text = "Header\nPython      developer\nBuilt reporting\n  tools."
        quote = "Python developer\nBuilt reporting tools."
        offset, restored = resolve_excerpt(source_text, quote)
        self.assertEqual(restored, "Python      developer\nBuilt reporting\n  tools.")
        self.assertEqual(source_text[offset:offset + len(restored)], restored)
        sources = [{"id": "pdf-page-1", "start": 0, "end": len(source_text), "page": 1, "method": "native"}]
        profile, evidence = validate_profile({"skills": [{"value": "Python developer", "evidence": [{"source_id": "pdf-page-1", "quote": quote}]}]}, source_text, sources)
        self.assertEqual(profile["skills"][0]["evidence"][0]["quote"], restored)
        self.assertEqual(evidence[0]["quote"], restored)
        self.assertEqual(validate_profile(profile, source_text, sources), (profile, evidence))
        with self.assertRaises(ValueError): resolve_excerpt(source_text, "Python expert")
        with self.assertRaises(ValueError): resolve_excerpt("Python  developer; Python\ndeveloper", "Python developer")

    def test_unsupported_claims_become_unknown_without_losing_verified_claims(self):
        from app.services.cv_profile_schema import retain_supported_claims
        content = "Python developer"
        sources = [{"id": "pdf-page-1", "start": 0, "end": len(content), "page": 1, "method": "native"}]
        payload = {"skills": [claim("Python"), claim("Invented")], "education": [{"qualification": claim("PhD")}]}
        profile, evidence, omitted = retain_supported_claims(json.dumps(payload), content, sources)
        self.assertEqual(omitted, 2)
        self.assertEqual([c["value"] for c in profile["skills"]], ["Python"])
        self.assertIsNone(profile["education"][0]["qualification"])
        self.assertEqual(len(evidence), 1)
        with self.assertRaises(ValueError): retain_supported_claims(json.dumps({"skills": [claim("Invented")]}), content, sources)
    async def test_bounded_malformed_repair_and_oversized(self):
        from app.services.cv_llm import extract_profile, check_input_size
        source = [{"id": "pdf-page-1", "start": 0, "end": 6, "page": 1, "method": "native"}]
        response = unittest.mock.Mock()
        response.json.return_value = {"message": {"content": "malformed"}}
        client = AsyncMock(); client.post.return_value = response
        context = AsyncMock(); context.__aenter__.return_value = client
        with patch("app.services.cv_llm.httpx.AsyncClient", return_value=context):
            with self.assertRaisesRegex(ValueError, "MODEL_SCHEMA_OR_EVIDENCE_INVALID"):
                await extract_profile("Python", source)
        self.assertEqual(client.post.await_count, 2)
        client.post.reset_mock()
        response.json.side_effect = [{"message": {"content": "malformed"}},
                                    {"message": {"content": json.dumps({"skills": [claim("Python")]})}}]
        with patch("app.services.cv_llm.httpx.AsyncClient", return_value=context):
            profile, evidence = await extract_profile("Python", source)
        self.assertEqual(profile["skills"][0]["value"], "Python")
        self.assertEqual(len(evidence), 1)
        self.assertEqual(client.post.await_count, 2)
        huge = "X" * 100000
        with self.assertRaisesRegex(ValueError, "MODEL_INPUT_TOO_LARGE"):
            check_input_size(huge, [{**source[0], "end": len(huge)}])


if __name__ == "__main__":
    unittest.main()
