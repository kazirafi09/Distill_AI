"""Private subprocess entry point; paths originate exclusively from stored IDs."""
import json
import sys
from pathlib import Path

from app.services.cv.text import extract_native, ocr_sources
from app.services.documents.extraction import DocumentExtractionError


def main():
    path, mode, input_path, output_path, language, timeout, document_timeout, parent_pid = sys.argv[1:]
    if sys.platform == "linux":
        import ctypes
        import os
        import resource
        import signal
        def stop_group(signum, frame):
            if os.getpgrp() == os.getpid():
                os.killpg(os.getpgrp(), signal.SIGKILL)
            raise SystemExit(1)
        signal.signal(signal.SIGALRM, stop_group)
        signal.signal(signal.SIGTERM, stop_group)
        signal.setitimer(signal.ITIMER_REAL, float(document_timeout))
        # Also clean up renderer/OCR children if the backend itself disappears.
        ctypes.CDLL(None).prctl(1, signal.SIGTERM, 0, 0, 0)
        # The durable Docker worker legitimately runs as PID 1. Detect a dead
        # parent by comparing the expected PID, rather than rejecting PID 1.
        if os.getppid() != int(parent_pid):
            stop_group(signal.SIGTERM, None)
        # Malformed decompression/rendering cannot exhaust the laptop's RAM.
        resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024 * 1024, 1536 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024, 32 * 1024 * 1024))
    try:
        result = (extract_native(Path(path)) if mode == "native" else
                  ocr_sources(Path(path), json.loads(Path(input_path).read_text(encoding="utf-8")), language, int(timeout)))
    except DocumentExtractionError as exc:
        result = {"error": exc.code}
    except Exception:
        result = {"error": "INVALID_DOCUMENT"}
    Path(output_path).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
