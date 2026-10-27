"""Bounded, in-memory one-to-one face comparison. No image or embedding logging."""
from hashlib import sha256
from io import BytesIO
from math import isfinite
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import get_settings
from app.integrations.google_drive import drive_client, get_file_metadata

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
DETECTOR = "face_detection_yunet_2023mar.onnx"
RECOGNIZER = "face_recognition_sface_2021dec.onnx"


class FaceInputError(ValueError):
    """A stable, non-personal reason that the UI can explain."""


def decode_image(data):
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise FaceInputError("image_size")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format not in {"JPEG", "PNG"} or image.width * image.height > MAX_IMAGE_PIXELS:
                    raise FaceInputError("image_format")
                image = ImageOps.exif_transpose(image).convert("RGB")
                image.thumbnail((1280, 1280))
                return image.copy()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise FaceInputError("image_format") from None


def download_reference(file_id):
    with drive_client() as client:
        metadata = get_file_metadata(client, file_id)
        if metadata.get("trashed") or metadata.get("mimeType") not in {"image/jpeg", "image/png"}:
            raise FaceInputError("reference_unusable")
        if not 0 < int(metadata.get("size", 0)) <= MAX_IMAGE_BYTES:
            raise FaceInputError("reference_unusable")
        data = bytearray()
        with client.stream("GET", f"files/{file_id}", params={"alt": "media", "supportsAllDrives": "true"}) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes(64 * 1024):
                data.extend(chunk)
                if len(data) > MAX_IMAGE_BYTES:
                    raise FaceInputError("reference_unusable")
        return bytes(data)


def model_files():
    root = get_settings().face_model_path
    paths = [root / name for name in (DETECTOR, RECOGNIZER)]
    if not all(path.is_file() for path in paths):
        raise RuntimeError("Face models are not installed.")
    return paths


def available():
    try:
        import cv2  # Optional until the face verification dependencies are installed.
        model_files()
        return hasattr(cv2, "FaceRecognizerSF")
    except (ImportError, RuntimeError):
        return False


def compare_faces(reference_bytes, frame_bytes):
    import cv2
    try:
        return _compare_faces(reference_bytes, frame_bytes)
    except cv2.error:
        raise RuntimeError("Face processing failed.") from None


def count_faces(frame_bytes):
    """Check preview frames in memory without embeddings or a comparison attempt."""
    import cv2
    import numpy as np
    try:
        image = cv2.cvtColor(np.asarray(decode_image(frame_bytes)), cv2.COLOR_RGB2BGR)
        detector_path, _ = model_files()
        detector = cv2.FaceDetectorYN.create(str(detector_path), "", (image.shape[1], image.shape[0]), 0.9)
        _, faces = detector.detect(image)
        return 0 if faces is None else len(faces)
    except cv2.error:
        raise RuntimeError("Face detection failed.") from None


def _compare_faces(reference_bytes, frame_bytes):
    import cv2
    import numpy as np

    settings = get_settings()
    detector_path, recognizer_path = model_files()
    detector = cv2.FaceDetectorYN.create(str(detector_path), "", (320, 320), 0.9)
    recognizer = cv2.FaceRecognizerSF.create(str(recognizer_path), "")

    def embedding(data, reference=False):
        try:
            image = cv2.cvtColor(np.asarray(decode_image(data)), cv2.COLOR_RGB2BGR)
            detector.setInputSize((image.shape[1], image.shape[0]))
            _, faces = detector.detect(image)
            if faces is None or len(faces) != 1:
                raise FaceInputError("no_face" if faces is None or len(faces) == 0 else "multiple_faces")
            x, y, width, height = faces[0, :4].astype(int)
            crop = image[max(0, y):max(0, y) + height, max(0, x):max(0, x) + width]
            if min(width, height) < 80 or not crop.size:
                raise FaceInputError("face_too_small")
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            if cv2.Laplacian(gray, cv2.CV_64F).var() < 20 or not 30 < gray.mean() < 230:
                raise FaceInputError("poor_quality")
            return recognizer.feature(recognizer.alignCrop(image, faces[0]))
        except FaceInputError:
            if reference:
                raise FaceInputError("reference_unusable") from None
            raise

    similarity = float(recognizer.match(embedding(reference_bytes, True), embedding(frame_bytes), cv2.FaceRecognizerSF_FR_COSINE))
    if not isfinite(similarity):
        raise FaceInputError("poor_quality")
    version = "yunet+sface:" + ":".join(sha256(path.read_bytes()).hexdigest() for path in (detector_path, recognizer_path))
    threshold = settings.face_match_threshold
    return {"status": "unable_to_verify" if threshold is None else "matched" if similarity >= threshold else "not_identified",
            "reason": "threshold_unconfigured" if threshold is None else None,
            "similarity": similarity, "threshold": settings.face_match_threshold, "model_version": version}
