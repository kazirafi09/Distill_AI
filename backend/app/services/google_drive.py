import os
import re
from contextlib import contextmanager
from functools import partial
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterator
from uuid import UUID, uuid4
from zipfile import ZipFile

import httpx
import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from app.core.config import get_settings
from app.services.document_extraction import (
    MAX_DOCX_EXPANDED_BYTES,
    MAX_FILE_BYTES,
)


DRIVE_API_URL = "https://www.googleapis.com/drive/v3/"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
FILE_FIELDS = "id,name,mimeType,size,trashed,sha256Checksum"
SUPPORTED_TYPES = {
    ".pdf": "application/pdf",
    ".docx": (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    ),
}


def validate_drive_id(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid Google Drive ID.")


@contextmanager
def drive_client() -> Iterator[httpx.Client]:
    settings = get_settings()
    path = settings.google_application_credentials

    if path is None or not path.is_file():
        raise RuntimeError("Google Drive credential file is unavailable.")

    credentials = service_account.Credentials.from_service_account_file(
        str(path),
        scopes=[DRIVE_SCOPE],
    )

    with requests.Session() as session:
        credentials.refresh(
            partial(
                Request(session=session),
                timeout=settings.google_drive_timeout_seconds,
            )
        )

    with httpx.Client(
        base_url=DRIVE_API_URL,
        headers={"Authorization": f"Bearer {credentials.token}"},
        timeout=settings.google_drive_timeout_seconds,
    ) as client:
        yield client


def get_file_metadata(client: httpx.Client, file_id: str) -> dict:
    validate_drive_id(file_id)
    response = client.get(
        f"files/{file_id}",
        params={"fields": FILE_FIELDS, "supportsAllDrives": "true"},
    )
    response.raise_for_status()
    return response.json()


def list_folder_files(folder_id: str) -> list[dict]:
    validate_drive_id(folder_id)
    files: list[dict] = []

    with drive_client() as client:
        folder = get_file_metadata(client, folder_id)
        if (
            folder.get("trashed")
            or folder["mimeType"] != "application/vnd.google-apps.folder"
        ):
            raise ValueError("The source must be an active Drive folder.")

        params = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": f"nextPageToken,files({FILE_FIELDS})",
            "pageSize": 100,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }

        while True:
            response = client.get("files", params=params)
            response.raise_for_status()
            payload = response.json()
            files.extend(payload.get("files", []))

            if not payload.get("nextPageToken"):
                return files
            params["pageToken"] = payload["nextPageToken"]


def validate_saved_document(path: Path, suffix: str) -> None:
    if suffix == ".pdf":
        with path.open("rb") as file:
            if b"%PDF-" not in file.read(1024):
                raise ValueError("The downloaded file is not a PDF.")
    else:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if (
                len(entries) > 1000
                or sum(entry.file_size for entry in entries)
                > MAX_DOCX_EXPANDED_BYTES
            ):
                raise ValueError("The DOCX contents exceed processing limits.")
            if "word/document.xml" not in archive.namelist():
                raise ValueError("The downloaded file is not a DOCX.")


def download_cv(
    file_id: str,
    storage_id: UUID | None = None,
    expected_source_sha256: str | None = None,
) -> dict:
    storage_id = storage_id or uuid4()
    root = get_settings().cv_storage_path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None

    try:
        with drive_client() as client:
            metadata = get_file_metadata(client, file_id)
            if (
                expected_source_sha256
                and metadata.get("sha256Checksum") != expected_source_sha256
            ):
                raise ValueError("Source changed after listing; fetch again.")
            filename = metadata["name"]
            suffix = Path(filename).suffix.lower()

            if metadata.get("trashed"):
                raise ValueError("The source file is trashed.")
            if (
                suffix not in SUPPORTED_TYPES
                or metadata["mimeType"] != SUPPORTED_TYPES[suffix]
            ):
                raise ValueError("Only PDF and DOCX CVs are supported.")

            expected_size = int(metadata.get("size", 0))
            if not 0 < expected_size <= MAX_FILE_BYTES:
                raise ValueError("CV must be nonempty and at most 10 MB.")

            relative_path = f"{storage_id}{suffix}"
            final_path = root / relative_path
            if final_path.exists():
                raise FileExistsError("This storage ID already has a saved CV.")

            checksum = sha256()
            size = 0

            with NamedTemporaryFile(
                dir=root, prefix=f"{storage_id}-", suffix=".part", delete=False
            ) as output:
                temporary_path = Path(output.name)
                with client.stream(
                    "GET",
                    f"files/{file_id}",
                    params={"alt": "media", "supportsAllDrives": "true"},
                ) as response:
                    response.raise_for_status()
                    for chunk in response.iter_bytes(chunk_size=64 * 1024):
                        size += len(chunk)
                        if size > MAX_FILE_BYTES:
                            raise ValueError("CV exceeds the 10 MB limit.")
                        output.write(chunk)
                        checksum.update(chunk)

                output.flush()
                os.fsync(output.fileno())

            if size != expected_size:
                raise ValueError("Downloaded size does not match the source.")

            source_sha256 = metadata.get("sha256Checksum")
            if source_sha256 and checksum.hexdigest() != source_sha256:
                raise ValueError("Downloaded checksum does not match the source.")

            validate_saved_document(temporary_path, suffix)
            os.replace(temporary_path, final_path)

            return {
                "storage_id": str(storage_id),
                "relative_path": relative_path,
                "drive_file_id": file_id,
                "original_filename": filename,
                "mime_type": metadata["mimeType"],
                "file_size_bytes": size,
                "file_sha256": checksum.hexdigest(),
                "source_sha256": source_sha256,
                "status": "downloaded",
            }
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)