"""
OCR any Ayurvedic textbook PDF via Google Document AI.

Splits the PDF into 15-page chunks (Document AI inline limit), OCRs each,
then stitches them into a single .txt file in data/ocr/.

Resume-safe: skips part files whose .txt already exists.

Usage:
    # Charaka vol 1
    PYTHONPATH=src .venv/bin/python -m ayurpost.kb.ocr_book \\
        --pdf ayur-text-books/charaka-samhita-vol-1.pdf \\
        --slug charaka-samhita-vol1

    # Ashtanga Hridayam
    PYTHONPATH=src .venv/bin/python -m ayurpost.kb.ocr_book \\
        --pdf ayur-text-books/Astanga-hrdayam-Eng.pdf \\
        --slug ashtanga-hridayam-vol1

    # dry-run (count pages, print plan, no API calls)
    PYTHONPATH=src .venv/bin/python -m ayurpost.kb.ocr_book \\
        --pdf ayur-text-books/charaka-samhita-vol-1.pdf \\
        --slug charaka-samhita-vol1 --dry-run

Output:
    data/ocr/<slug>_part<NNN>_p<XXXX>-<YYYY>.txt   (per 15-page chunk)
    data/ocr/<slug>.txt                              (stitched)

Prerequisites:
    GCP_PROJECT_ID, DOC_AI_LOCATION (default "us"), DOC_AI_PROCESSOR_ID in env.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import time
from pathlib import Path

from google.api_core.client_options import ClientOptions
from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import documentai
import pypdf

OUTPUT_DIR = Path("data/ocr")
CHUNK_PAGES = 15          # Document AI inline limit
MIME_TYPE = "application/pdf"


def _get_client(location: str) -> documentai.DocumentProcessorServiceClient:
    opts = ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com")
    return documentai.DocumentProcessorServiceClient(client_options=opts)


def _processor_name(project_id: str, location: str, processor_id: str) -> str:
    return f"projects/{project_id}/locations/{location}/processors/{processor_id}"


def _ocr_bytes(client, name: str, pdf_bytes: bytes) -> str:
    raw = documentai.RawDocument(content=pdf_bytes, mime_type=MIME_TYPE)
    result = client.process_document(
        request=documentai.ProcessRequest(name=name, raw_document=raw))
    return result.document.text


def _split_pdf(pdf_path: Path, chunk_size: int) -> list[tuple[int, int, bytes]]:
    """Return list of (start_page, end_page, pdf_bytes) for each chunk (1-indexed)."""
    reader = pypdf.PdfReader(str(pdf_path))
    total = len(reader.pages)
    chunks = []
    for start in range(0, total, chunk_size):
        end = min(start + chunk_size - 1, total - 1)
        writer = pypdf.PdfWriter()
        for i in range(start, end + 1):
            writer.add_page(reader.pages[i])
        buf = io.BytesIO()
        writer.write(buf)
        chunks.append((start + 1, end + 1, buf.getvalue()))
    return chunks


def _stitch(slug: str, out_dir: Path) -> Path | None:
    parts = sorted(out_dir.glob(f"{slug}_part*.txt"))
    if not parts:
        return None
    out = out_dir / f"{slug}.txt"
    with out.open("w", encoding="utf-8") as f:
        for p in parts:
            page_range = p.stem.split("_")[-1]
            f.write(f"\n\n========== {p.stem} ({page_range}) ==========\n\n")
            f.write(p.read_text(encoding="utf-8"))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pdf", required=True, help="Path to the source PDF")
    parser.add_argument("--slug", required=True,
                        help="Output slug, e.g. charaka-samhita-vol1. "
                             "Parts written as <slug>_part<NNN>_p<XXXX>-<YYYY>.txt")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count pages and print plan; make no API calls")
    parser.add_argument("--chunk-pages", type=int, default=CHUNK_PAGES,
                        help=f"Pages per Doc AI request (default {CHUNK_PAGES})")
    parser.add_argument("--limit", type=int,
                        help="OCR only the first N pending chunks (test mode)")
    parser.add_argument("--sleep", type=float, default=0.5,
                        help="Seconds between requests (default 0.5)")
    parser.add_argument("--project-id", default=os.getenv("GCP_PROJECT_ID"))
    parser.add_argument("--location", default=os.getenv("DOC_AI_LOCATION", "us"))
    parser.add_argument("--processor-id", default=os.getenv("DOC_AI_PROCESSOR_ID"))
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    print(f"reading PDF: {pdf_path} ({pdf_path.stat().st_size / 1_048_576:.1f} MB)")
    chunks = _split_pdf(pdf_path, args.chunk_pages)
    total_pages = chunks[-1][1] if chunks else 0
    print(f"pages: {total_pages}  →  {len(chunks)} chunks of ≤{args.chunk_pages} pages")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Build work list
    work = []
    for idx, (s, e, pdf_bytes) in enumerate(chunks, 1):
        part_name = f"{args.slug}_part{idx:03d}_p{s:04d}-{e:04d}"
        out_txt = OUTPUT_DIR / f"{part_name}.txt"
        work.append((part_name, out_txt, pdf_bytes))

    pending = [(n, o, b) for n, o, b in work if not o.exists()]
    done = len(work) - len(pending)
    if args.limit:
        pending = pending[:args.limit]

    print(f"already done: {done}  pending: {len(pending)}")

    if args.dry_run:
        print("\n[dry-run] chunks that would be OCR'd:")
        for n, o, _ in pending[:10]:
            print(f"  {n}.txt")
        if len(pending) > 10:
            print(f"  ... and {len(pending) - 10} more")
        print(f"\noutput: {OUTPUT_DIR}/{args.slug}.txt")
        print("[dry-run] no API calls made.")
        return 0

    if not args.project_id or not args.processor_id:
        print("ERROR: GCP_PROJECT_ID and DOC_AI_PROCESSOR_ID must be set.",
              file=sys.stderr)
        return 1

    if not pending:
        print("nothing to do — stitching...")
    else:
        client = _get_client(args.location)
        name = _processor_name(args.project_id, args.location, args.processor_id)
        print(f"processor: {name}\n")

        failures = []
        for i, (part_name, out_txt, pdf_bytes) in enumerate(pending, 1):
            print(f"[{i}/{len(pending)}] {part_name} ... ", end="", flush=True)
            try:
                text = _ocr_bytes(client, name, pdf_bytes)
                out_txt.write_text(text, encoding="utf-8")
                print(f"ok ({len(text):,} chars)")
            except (GoogleAPICallError, RetryError) as exc:
                print(f"FAILED: {exc}")
                failures.append((part_name, str(exc)))
            time.sleep(args.sleep)

        if failures:
            print(f"\n{len(failures)} failure(s) — re-run to retry:")
            for n, e in failures:
                print(f"  {n}: {e}")

    print(f"\nstitching {args.slug}.txt ...")
    stitched = _stitch(args.slug, OUTPUT_DIR)
    if stitched:
        print(f"  → {stitched}  ({stitched.stat().st_size:,} bytes)")
    else:
        print("  (no parts found to stitch yet)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
