"""Prepare a local synthetic transcript preview; no DB, provider or model calls."""

import argparse
import json
from pathlib import Path

from app.services.interviews.transcript_preparation import PreparationError, load_fixture


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript", required=True, type=Path)
    parser.add_argument("--timeline", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="Optional new JSON file; existing files are never overwritten.")
    args = parser.parse_args(argv)
    try:
        prepared = load_fixture(args.transcript, args.timeline)
        if args.output:
            # Exclusive creation prevents overwriting fixtures or other files.
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(prepared, indent=2, ensure_ascii=False) + "\n")
    except PreparationError as error:
        parser.exit(1, f"Preparation failed: {error}\n")
    except OSError:
        parser.exit(1, "Preparation failed: OUTPUT_UNAVAILABLE\n")
    turns = prepared["turns"]
    print(json.dumps({
        "fixture_id": prepared["fixture_id"], "fixture_revision": prepared["fixture_revision"],
        "status": prepared["status"], "f5_ready": prepared["f5_ready"],
        "turn_count": len(turns), "stream_count": len({turn["stream_id"] for turn in turns}),
        "roles": sorted({turn["speaker_role"] for turn in turns}),
        "duration_ms": max(turn["session_end_ms"] for turn in turns),
        "aligned_content_sha256": prepared["aligned_content_sha256"],
        "output_written": args.output is not None,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
