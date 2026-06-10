"""
OCR all Sushruta Samhita PDF chunks via Google Document AI.

Prerequisites:
  1. Create a Document AI processor (type: "Document OCR") in your GCP project.
  2. Auth: run `gcloud auth application-default login` once.
  3. Set the three env vars below (or pass via CLI flags).

Usage:
    export GCP_PROJECT_ID="your-project-id"
    export DOC_AI_LOCATION="us"            # or "asia-south1"
    export DOC_AI_PROCESSOR_ID="abc123..."  # from the processor URL

    python -m ayurpost.kb.ocr_sushruta

Output:
    data/ocr/sushruta-samhita-vol{N}_part{NNN}_p{XXXX}-{YYYY}.txt   (per chunk)
    data/ocr/sushruta-samhita-vol{N}.txt                            (stitched per volume)

Resume-safe: skips chunks whose output .txt already exists.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from google.api_core.client_options import ClientOptions
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import documentai


CHUNKS_DIR = Path("ayur-text-books/sushruta-chunks")
OUTPUT_DIR = Path("data/ocr")
MIME_TYPE = "application/pdf"


def get_client(location: str) -> documentai.DocumentProcessorServiceClient:
    opts = ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com")
    return documentai.DocumentProcessorServiceClient(client_options=opts)


def processor_path(project_id: str, location: str, processor_id: str) -> str:
    return f"projects/{project_id}/locations/{location}/processors/{processor_id}"


def ocr_pdf(
    client: documentai.DocumentProcessorServiceClient,
    name: str,
    pdf_path: Path,
) -> str:
    raw_document = documentai.RawDocument(
        content=pdf_path.read_bytes(),
        mime_type=MIME_TYPE,
    )
    request = documentai.ProcessRequest(name=name, raw_document=raw_document)
    result = client.process_document(request=request)
    return result.document.text


def stitch_volume(volume_num: int, output_dir: Path) -> Path:
    """Concatenate all chunk .txt files for a volume into one file, in page order."""
    chunks = sorted(output_dir.glob(f"sushruta-samhita-vol{volume_num}_part*.txt"))
    if not chunks:
        return None
    stitched_path = output_dir / f"sushruta-samhita-vol{volume_num}.txt"
    with stitched_path.open("w") as out:
        for chunk in chunks:
            page_range = chunk.stem.split("_")[-1]  # "p0001-0025"
            out.write(f"\n\n========== {chunk.stem} ({page_range}) ==========\n\n")
            out.write(chunk.read_text())
    return stitched_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", default=os.getenv("GCP_PROJECT_ID"))
    parser.add_argument("--location", default=os.getenv("DOC_AI_LOCATION", "us"))
    parser.add_argument("--processor-id", default=os.getenv("DOC_AI_PROCESSOR_ID"))
    parser.add_argument(
        "--volume",
        type=int,
        choices=[1, 2, 3],
        help="OCR only this volume (default: all 3)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="OCR only the first N pending chunks (useful for a test run)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.5,
        help="Seconds to sleep between requests (default 0.5)",
    )
    args = parser.parse_args()

    if not args.project_id or not args.processor_id:
        print(
            "ERROR: GCP_PROJECT_ID and DOC_AI_PROCESSOR_ID must be set "
            "(via env or --project-id / --processor-id).",
            file=sys.stderr,
        )
        return 1

    if not CHUNKS_DIR.exists():
        print(f"ERROR: chunks directory not found: {CHUNKS_DIR}", file=sys.stderr)
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    pattern = (
        f"sushruta-samhita-vol{args.volume}_part*.pdf"
        if args.volume
        else "sushruta-samhita-vol*_part*.pdf"
    )
    all_chunks = sorted(CHUNKS_DIR.glob(pattern))
    if not all_chunks:
        print(f"ERROR: no chunks match pattern {pattern} in {CHUNKS_DIR}", file=sys.stderr)
        return 1

    pending = [
        chunk for chunk in all_chunks
        if not (OUTPUT_DIR / f"{chunk.stem}.txt").exists()
    ]
    if args.limit:
        pending = pending[: args.limit]

    done_count = len(all_chunks) - len([
        c for c in all_chunks if not (OUTPUT_DIR / f"{c.stem}.txt").exists()
    ])

    print(f"Total chunks matching: {len(all_chunks)}")
    print(f"Already OCR'd: {done_count}")
    print(f"Pending this run: {len(pending)}")

    if not pending:
        print("Nothing to do.")
    else:
        client = get_client(args.location)
        name = processor_path(args.project_id, args.location, args.processor_id)
        print(f"Processor: {name}\n")

        failures = []
        for idx, chunk in enumerate(pending, 1):
            out_path = OUTPUT_DIR / f"{chunk.stem}.txt"
            print(f"[{idx}/{len(pending)}] {chunk.name} ... ", end="", flush=True)
            try:
                text = ocr_pdf(client, name, chunk)
                out_path.write_text(text)
                print(f"ok ({len(text)} chars)")
            except (GoogleAPICallError, RetryError) as exc:
                print(f"FAILED: {exc}")
                failures.append((chunk.name, str(exc)))
            time.sleep(args.sleep)

        if failures:
            print(f"\n{len(failures)} failures:")
            for name_, err in failures:
                print(f"  {name_}: {err}")

    print("\nStitching per-volume files...")
    for vol in [1, 2, 3]:
        if args.volume and vol != args.volume:
            continue
        stitched = stitch_volume(vol, OUTPUT_DIR)
        if stitched:
            print(f"  vol{vol}: {stitched} ({stitched.stat().st_size:,} bytes)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
