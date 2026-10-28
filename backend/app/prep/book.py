"""Render the textbook pages a lecture follows, for its doubt answers (app.context.book).

    .venv/bin/python -m app.prep.book "../data/books/university-physics/ch02.pdf" --slug lecture-05-video \
        --pages 71,74-77 --printed-offset 23 --title "Sears and Zemansky's University Physics, ch. 2"

Pages are numbered as in the PDF; `--printed-offset` turns them into the numbers printed in the book (PDF 74 is
printed p.51 with offset 23). Needs poppler's `pdftoppm`. Rerunning replaces the lesson's earlier pages.
"""

import argparse
import subprocess
from pathlib import Path

from app.context.book import MANIFEST, Book, BookPage, book_dir

# Long side in pixels: about 1.15 megapixels for a textbook page, the most Claude reads without scaling it down
LONG_SIDE = 1180
JPEG_QUALITY = 90


def parse_pages(spec: str) -> list[int]:
    """`71,74-77` -> [71, 74, 75, 76, 77]"""
    pages: list[int] = []
    for part in spec.split(","):
        first, _, last = part.strip().partition("-")
        pages += range(int(first), int(last or first) + 1)
    return pages


def render(pdf: Path, page: int, out: Path) -> None:
    """One PDF page as a JPEG at `out` (pdftoppm adds the extension)."""
    subprocess.run(
        ["pdftoppm", "-f", str(page), "-l", str(page), "-singlefile", "-jpeg", "-jpegopt", f"quality={JPEG_QUALITY}",
         "-scale-to", str(LONG_SIDE), str(pdf), str(out.with_suffix(""))],
        check=True,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--pages", required=True, help="PDF page numbers, e.g. 71,74-77")
    ap.add_argument("--printed-offset", type=int, default=0, help="PDF page minus printed page number")
    ap.add_argument("--title", required=True)
    args = ap.parse_args()

    out_dir = book_dir(args.slug)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("p*.jpg"):
        old.unlink()
    pages = []
    for n in parse_pages(args.pages):
        file = f"p{n:04d}.jpg"
        render(args.pdf, n, out_dir / file)
        pages.append(BookPage(pdf_page=n, label=str(n - args.printed_offset), file=file))
        print(f"page {n} -> {out_dir / file}")
    (out_dir / MANIFEST).write_text(Book(title=args.title, pages=pages).model_dump_json(indent=2))
    print(f"{len(pages)} pages in {out_dir / MANIFEST}")


if __name__ == "__main__":
    main()
