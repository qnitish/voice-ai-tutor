"""Notes and book PDFs as a TEXT answer-context source. Not wired up yet: `retrieve` returns `[]`.

Textbook pages already reach video answers as images (app.context.book); this stub is for text passages.

How to fill this in (callers need no change; `ContextService` already merges, dedupes and budgets passages):

1. Ingest (offline, a prep stage per lesson, e.g. `python -m app.prep.documents --slug … notes.pdf book-ch4.pdf`):
   - Extract the text of every page (pypdf / pdfplumber). For figures and diagrams, render the page region to an
     image and ask the vision model (`llm`) for a one-paragraph description, appended to that page's text with a
     "Figure:" prefix so it can be matched like prose.
   - Chunk each page into ~400-token pieces with a ~50-token overlap, keeping chapter headings in each chunk's first
     line so a chunk is understandable on its own.
   - Embed every chunk (Azure OpenAI embeddings deployment) and store it in a `document_chunks` table
     (`id, lesson_id -> lessons.id, document: str` (file name), `page: int`, `idx: int`, `text: str`,
     `embedding: Vector` via pgvector, `created_at`). One row per chunk; re-ingesting a document deletes its old rows.
     Keep the rows tied to the lesson version, like scenes and chapters, so a republished lesson gets re-ingested.

2. Retrieve (`retrieve(q, k)`): embed `q.question` (prefix it with the current chapter's title and key terms if
   `q.chapter_idx` is set, which helps short questions like "why does it go up?"), then query the `k` nearest chunks
   for `q.lesson_id` by cosine distance (`ORDER BY embedding <=> :vec LIMIT :k`). Return each as
   `Passage(source="documents", ref=f"{document} p.{page}", text=chunk.text, score=similarity)`, with scores kept
   <= 1.0 so the transcript window (3.0) and chapter (2.0) still come first.

3. Switch it on with `CONTEXT_SOURCES=transcript,documents` in `.env`; `service.SOURCES` already knows the name.
"""

from app.context.base import ContextQuery, Passage


class DocumentSource:
    name = "documents"

    async def retrieve(self, q: ContextQuery, k: int) -> list[Passage]:
        return []
