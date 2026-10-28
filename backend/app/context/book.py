"""Textbook pages as images for the video answer prompt.

`python -m app.prep.book` renders the pages a lecture follows into `data/lessons/<slug>/book/` with a `book.json`
listing them. Every doubt of the lesson gets the same pages, placed before the doubt and marked as a cacheable
prefix, so after the first doubt the provider reads them from its cache. A lesson without `book.json` gets none.
"""

import base64
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from app.config import settings

MANIFEST = "book.json"


class BookPage(BaseModel):
    pdf_page: int
    label: str  # the page number printed in the book
    file: str
    media_type: str = "image/jpeg"


class Book(BaseModel):
    title: str
    pages: list[BookPage]


def book_dir(slug: str) -> Path:
    return settings.lessons_dir / slug / "book"


def load_book(slug: str) -> Book | None:
    path = book_dir(slug) / MANIFEST
    return Book.model_validate_json(path.read_text()) if path.exists() else None


def book_parts(slug: str) -> list[dict]:
    """Message parts for the lesson's book pages (llm_base.Messages), [] when it has none."""
    path = book_dir(slug) / MANIFEST
    if not path.exists():
        return []
    return _parts(slug, path.stat().st_mtime_ns)


@lru_cache(maxsize=32)
def _parts(slug: str, _mtime: int) -> list[dict]:
    """Read and encode the pages once per manifest version (`_mtime` keys the cache)."""
    book = load_book(slug)
    if book is None or not book.pages:
        return []
    labels = ", ".join(p.label for p in book.pages)
    parts: list[dict] = [
        {
            "type": "text",
            "text": f"Textbook pages this lecture follows ({book.title}; pages {labels}), for reference. "
            "The student's doubt and the lecture context come after them.",
        }
    ]
    for page in book.pages:
        data = base64.b64encode((book_dir(slug) / page.file).read_bytes()).decode()
        parts += [
            {"type": "text", "text": f"Book p.{page.label}:"},
            {"type": "image", "media_type": page.media_type, "data": data},
        ]
    parts[-1]["cache"] = True
    return parts
