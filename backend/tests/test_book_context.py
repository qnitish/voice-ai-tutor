"""Textbook pages as images for video doubt answers (app.context.book, app.prep.book)."""

import asyncio
import base64
import shutil

import pytest

import app.llm
from app.config import settings
from app.context.book import Book, BookPage, book_dir, book_parts
from app.prep.book import parse_pages
from app.providers.anthropic_llm import content_blocks
from app.providers.openai_llm import content_parts
from tests.conftest import answer, create_guest, create_session

JPEG = b"\xff\xd8\xff\xe0fake-page\xff\xd9"


def write_book(slug: str, labels: list[str]) -> None:
    folder = book_dir(slug)
    folder.mkdir(parents=True, exist_ok=True)
    pages = []
    for i, label in enumerate(labels):
        (folder / f"p{i}.jpg").write_bytes(JPEG)
        pages.append(BookPage(pdf_page=i + 1, label=label, file=f"p{i}.jpg"))
    (folder / "book.json").write_text(Book(title="Physics ch. 2", pages=pages).model_dump_json())


@pytest.fixture
def remove_book():
    slugs: list[str] = []
    yield slugs
    for slug in slugs:
        shutil.rmtree(book_dir(slug), ignore_errors=True)


def test_parse_pages():
    assert parse_pages("71,74-77") == [71, 74, 75, 76, 77]
    assert parse_pages("5") == [5]


def test_no_book_no_parts():
    assert book_parts("no-such-lesson") == []


def test_book_parts_label_each_page_and_end_a_cached_prefix(remove_book):
    remove_book.append("book-lesson")
    write_book("book-lesson", ["51", "52"])
    parts = book_parts("book-lesson")
    assert "Physics ch. 2" in parts[0]["text"] and "51, 52" in parts[0]["text"]
    images = [p for p in parts if p["type"] == "image"]
    assert len(images) == 2 and base64.b64decode(images[0]["data"]) == JPEG
    assert {"type": "text", "text": "Book p.52:"} in parts
    assert parts[-1]["type"] == "image" and parts[-1]["cache"] is True
    assert sum(bool(p.get("cache")) for p in parts) == 1


def test_anthropic_blocks_carry_images_and_cache_mark():
    blocks = content_blocks(
        [{"type": "image", "media_type": "image/jpeg", "data": "QUJD", "cache": True}, {"type": "text", "text": "doubt"}]
    )
    assert blocks[0] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"},
        "cache_control": {"type": "ephemeral"},
    }
    assert blocks[1] == {"type": "text", "text": "doubt"}
    assert content_blocks("plain") == "plain"


def test_openai_parts_send_images_as_data_urls():
    parts = content_parts([{"type": "image", "media_type": "image/jpeg", "data": "QUJD", "cache": True}])
    assert parts == [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}}]


def test_stream_lines_puts_the_prefix_before_the_prompt(monkeypatch):
    seen = {}

    class Provider:
        retryable = ()

        async def open_stream(self, system, messages, **kw):
            seen["messages"] = messages

            async def deltas():
                yield '{"say": "ok"}\n'

            return deltas()

    monkeypatch.setattr(app.llm, "get_llm", lambda: Provider())
    prefix = [{"type": "text", "text": "Book p.51:"}]

    async def collect():
        return [line async for line in app.llm.stream_lines("sys", "the doubt", prefix=prefix)]

    lines = asyncio.run(collect())
    assert lines == ['{"say": "ok"}']
    assert seen["messages"][0]["content"] == [prefix[0], {"type": "text", "text": "the doubt"}]


# --- the answer stream -----------------------------------------------------------------


@pytest.fixture
def prefixes(llm, monkeypatch):
    """Record the prefix of every answer stream (the shared fake takes none)."""
    seen: list = []

    async def stream_lines(system, user, temperature=0.5, model=None, prefix=None):
        seen.append(prefix)
        llm.stream_calls.append(dict(system=system, user=user))
        for line in llm.answer_lines:
            yield line

    monkeypatch.setattr(app.llm, "stream_lines", stream_lines)
    return seen


def video_doubt(client, llm) -> dict:
    from tests.test_video_api import ask_video, publish_video

    lesson = publish_video()
    session = create_session(client, create_guest(client), lesson.slug)
    llm.classify(intent="not_covered", question="is acceleration zero at the top?")
    return ask_video(client, session["id"], at_ms=90_000) | {"slug": lesson.slug}


def test_video_answer_gets_the_lesson_book_pages(client, llm, prefixes, remove_book):
    doubt = video_doubt(client, llm)
    remove_book.append(doubt["slug"])
    write_book(doubt["slug"], ["51", "54"])
    answer(client, doubt)
    assert prefixes[-1] and sum(p["type"] == "image" for p in prefixes[-1]) == 2


def test_book_pages_can_be_switched_off(client, llm, prefixes, remove_book, monkeypatch):
    monkeypatch.setattr(settings, "context_book_pages", False)
    doubt = video_doubt(client, llm)
    remove_book.append(doubt["slug"])
    write_book(doubt["slug"], ["51"])
    answer(client, doubt)
    assert prefixes[-1] is None


def test_video_lesson_without_a_book_answers_as_before(client, llm, prefixes):
    answer(client, video_doubt(client, llm))
    assert prefixes[-1] is None
