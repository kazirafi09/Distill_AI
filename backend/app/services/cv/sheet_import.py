import re
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cv import Candidate, CandidateApplication, CVDocument, CVFileVersion, CVSource
from app.integrations.google_drive import SUPPORTED_TYPES, list_folder_files, validate_drive_id


_email = TypeAdapter(EmailStr)
REQUIRED_HEADERS = ("Name", "Email", "CV File", "Image")


def source_lock_key(source_id: UUID) -> int:
    return int.from_bytes(sha256(source_id.bytes).digest()[:8], "big", signed=True)


def response_columns(headers: list[str]) -> dict[str, int]:
    normalized = [header.strip().casefold() for header in headers]
    columns = {}
    for header in (*REQUIRED_HEADERS, "Timestamp"):
        matches = [i for i, value in enumerate(normalized) if value == header.casefold()]
        if len(matches) > 1 or (not matches and header in REQUIRED_HEADERS):
            raise ValueError(f"The response tab needs exactly one '{header}' column.")
        if matches:
            columns[header] = matches[0]
    return columns


def drive_ids_from_cell(value: str) -> list[str]:
    # Form upload cells can contain multiple comma-separated Drive URLs.
    links = re.findall(r"https?://[^\s,]+", value)
    if not links:
        raise ValueError("The CV File cell must contain a Google Drive link.")
    ids = []
    for link in links:
        parsed = urlparse(link)
        if parsed.scheme != "https" or parsed.hostname != "drive.google.com":
            raise ValueError("The CV File cell contains an unsupported link.")
        match = re.fullmatch(r"/file/d/([A-Za-z0-9_-]+)(?:/[^?]*)?", parsed.path)
        file_id = match[1] if match else None
        if file_id is None and parsed.path in {"/open", "/uc"}:
            values = parse_qs(parsed.query).get("id", [])
            file_id = values[0] if len(values) == 1 else None
        if not file_id:
            raise ValueError("The CV File cell contains an invalid Drive file link.")
        validate_drive_id(file_id)
        if len(file_id) > 255:
            raise ValueError("Drive file ID is too long.")
        if file_id not in ids:
            ids.append(file_id)
    return ids


def parse_response_rows(sheet: dict) -> tuple[list[dict], list[dict]]:
    columns = response_columns(sheet["headers"])
    rows, errors = [], []
    for row in sheet["rows"]:
        def cell(header: str) -> str:
            index = columns.get(header)
            values = row["values"]
            return str(values[index]).strip() if index is not None and index < len(values) else ""

        try:
            name = cell("Name")
            if not name or len(name) > 255:
                raise ValueError("Name must contain between 1 and 255 characters.")
            # Normalize contact metadata; shared emails do not identify candidates.
            email = str(_email.validate_python(cell("Email"))).casefold()
            timestamp = cell("Timestamp") or None
            if timestamp and len(timestamp) > 100:
                raise ValueError("Timestamp is too long.")
            photo_ids = drive_ids_from_cell(cell("Image"))
            if len(photo_ids) != 1:
                raise ValueError("Image must contain exactly one Google Drive upload.")
            rows.append({
                "row_number": row["row_number"],
                "full_name": name,
                "email": email,
                "drive_file_ids": drive_ids_from_cell(cell("CV File")),
                "photo_file_id": photo_ids[0],
                "submitted_at_text": timestamp,
            })
        except (ValueError, ValidationError):
            # Report row numbers without echoing candidate personal information.
            errors.append({"row_number": row["row_number"], "error_code": "INVALID_RESPONSE_ROW"})
    return rows, errors


def list_source_cv_files(folder_id: str, recursive: bool) -> list[dict]:
    pending, visited, files = [folder_id], set(), {}
    while pending:
        folder = pending.pop()
        if folder in visited:
            continue
        visited.add(folder)
        if len(visited) > 100:
            raise ValueError("The CV source contains more than 100 folders.")
        for item in list_folder_files(folder):
            if item.get("trashed"):
                continue
            if item["mimeType"] == "application/vnd.google-apps.folder":
                if recursive:
                    pending.append(item["id"])
                continue
            suffix = Path(item["name"]).suffix.lower()
            if suffix in SUPPORTED_TYPES and item["mimeType"] == SUPPORTED_TYPES[suffix]:
                files[item["id"]] = item
    return list(files.values())


class RowImportError(ValueError):
    pass


async def import_response_rows(
    db: AsyncSession,
    source: CVSource,
    recruiter_id: UUID,
    rows: list[dict],
    allowed_file_ids: set[str],
    validation_errors: list[dict],
) -> dict:
    source_id, job_id = source.id, source.job_description_id
    from app.services.cv.deletion import excluded_files, file_key
    excluded = await excluded_files(db, source_id)
    result = {
        "rows_imported": 0, "rows_skipped": 0, "rows_failed": len(validation_errors),
        "candidates_created": 0, "applications_created": 0, "cvs_linked": 0,
        "errors": list(validation_errors),
    }
    for row in rows:
        if any(file_key(file_id) in excluded for file_id in row["drive_file_ids"]):
            result["rows_skipped"] += 1
            continue
        try:
            # Each row commits independently, so a restart safely resumes imports.
            async with db.begin_nested():
                if not set(row["drive_file_ids"]).issubset(allowed_file_ids):
                    raise RowImportError("CV_NOT_IN_SOURCE_OR_UNSUPPORTED")
                documents = []
                # Lock in a stable order, including when sources share CV files.
                for file_id in sorted(row["drive_file_ids"]):
                    document = await db.scalar(select(CVDocument).where(
                        CVDocument.job_description_id == job_id,
                        CVDocument.drive_file_id == file_id,
                    ).with_for_update().execution_options(populate_existing=True))
                    latest = None if document is None else await db.scalar(
                        select(CVFileVersion).where(CVFileVersion.cv_document_id == document.id)
                        .order_by(CVFileVersion.created_at.desc(), CVFileVersion.id.desc()).limit(1)
                    )
                    if latest is None or latest.status != "downloaded":
                        raise RowImportError("CV_NOT_DOWNLOADED")
                    if document.application_id is not None:
                        owner = await db.scalar(
                            select(Candidate).join(CandidateApplication, CandidateApplication.candidate_id == Candidate.id)
                            .where(CandidateApplication.id == document.application_id)
                        )
                        if owner is None or owner.recruiter_id != recruiter_id or owner.email != row["email"]:
                            raise RowImportError("CV_ALREADY_ASSIGNED")
                    documents.append(document)

                # Reuse only an application already linked to these CV files.
                # Different submissions may share an inbox and still need their
                # own screening results, shortlist entries and interview links.
                application_ids = {document.application_id for document in documents
                                   if document.application_id is not None}
                if len(application_ids) > 1:
                    raise RowImportError("CV_ALREADY_ASSIGNED")
                application_id = next(iter(application_ids), None)
                candidate_created = application_created = application_id is None
                if application_created:
                    candidate_id, application_id = uuid4(), uuid4()
                    db.add(Candidate(id=candidate_id, recruiter_id=recruiter_id,
                        full_name=row["full_name"], email=row["email"]))
                    await db.flush()
                    db.add(CandidateApplication(id=application_id, candidate_id=candidate_id,
                        job_description_id=job_id, source_id=source_id,
                        source_row_number=row["row_number"], submitted_at_text=row["submitted_at_text"]))
                    await db.flush()
                linked = 0
                if row.get("photo_file_id"):
                    from app.services.interviews.identification import link_reference
                    await link_reference(db, application_id, row["photo_file_id"])
                for document in documents:
                    if document.application_id is None:
                        document.application_id = application_id
                        linked += 1
                await db.flush()
            await db.commit()
        except RowImportError as exc:
            await db.rollback()
            result["rows_failed"] += 1
            result["errors"].append({"row_number": row["row_number"], "error_code": str(exc)})
            continue
        result["candidates_created"] += int(candidate_created)
        result["applications_created"] += int(application_created)
        result["cvs_linked"] += linked
        key = "rows_imported" if candidate_created or application_created or linked else "rows_skipped"
        result[key] += 1
    return result
