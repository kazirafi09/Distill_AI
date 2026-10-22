"""Install checksum-verified OpenCV Zoo models and preserve their licenses."""
from hashlib import sha256

import httpx

from app.core.config import get_settings
from app.services.interviews.face_matching import DETECTOR, RECOGNIZER

MODELS = (
    ("face_detection_yunet", DETECTOR, "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    ("face_recognition_sface", RECOGNIZER, "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
)


def main():
    root = get_settings().face_model_path
    root.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for directory, filename, checksum in MODELS:
            path = root / filename
            if not path.exists() or sha256(path.read_bytes()).hexdigest() != checksum:
                response = client.get(f"https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/{directory}/{filename}")
                response.raise_for_status()
                if sha256(response.content).hexdigest() != checksum:
                    raise RuntimeError(f"Checksum mismatch for {filename}; no model was installed.")
                path.write_bytes(response.content)
            license_response = client.get(f"https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/{directory}/LICENSE")
            license_response.raise_for_status()
            (root / f"{directory}.LICENSE").write_bytes(license_response.content)
            print(f"Verified {filename}")


if __name__ == "__main__":
    main()
