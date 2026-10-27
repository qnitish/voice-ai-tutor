# Video lesson spec (demo: Lecture 5, English)

The recorded lecture video plays as is. The student can interrupt at any time with a doubt; the tutor answers in the
teacher's cloned voice on a whiteboard overlay, grounded in the lecture (transcript now; notes and book PDFs later),
asks "Got it?", takes follow-ups, and recaps a long doubt before the video resumes. At checkpoints the video pauses
for 1–3 questions. Generated (scene) lessons keep working unchanged: a lesson has `kind` `"generated"` or `"video"`.

Shared contracts are already in the code; build against them and don't change them without saying so:
`backend/app/db.py` (Chapter, Checkpoint, DoubtThread, CheckpointAnswer, SessionEvent, new columns),
`backend/app/schemas.py` ("Video lessons" section, LessonOut/LessonSummaryOut `kind`, ResumeOut `position_ms`),
`backend/app/config.py` ("Video lessons" settings), `backend/app/telemetry.py`, `frontend/src/lib/types.ts`,
`frontend/src/lib/api.ts`, `frontend/src/store/video.ts`, `frontend/src/engine/host.ts`, and the skeletons
`frontend/src/engine/video/{checkpoints,videoTutor}.ts`.

## 1. Student flow

```
video plays ── mic ──► video pauses ──► doubt thread ──► (recap) ──► video resumes at resumePointMs
video plays ── checkpoint at_ms reached ──► video pauses ──► intro + 1..N questions ──► video resumes at at_ms
```

### 1.1 Doubt thread
1. Mic pressed: the video pauses (fade the audio like the scene player). Recording, STT and routing as today.
2. The server returns an instant fixed **ack** (cached audio) and routes the doubt:

   | intent | answer_when | player does |
   |---|---|---|
   | prerequisite, confused | `now` | stream the answer on the overlay now |
   | not_covered | `chapter_end` | ack, resume; at the current chapter's `end_ms` pause and answer |
   | covered_later | `target_chapter` | ack, resume; at `target_ms` (target chapter start) play the callback |
   | repeat | `replay` | seek to `replay_from_ms` (current chapter start, or 30 s back if the chapter started earlier) and play |
   | continue | `none` | in an open thread this means "understood": the thread closes (see 4) |
   | off_topic, unclear, clarify | `none` | play the ack, keep the thread open if one is open, else resume |

3. **Framing**: the answer's first spoken line acknowledges the doubt and places it in what is being studied, then
   leads in, e.g. "So you're asking why the packet first goes up. Right now we're studying motion under gravity, so
   let's see it with an example." It is part of the streamed answer (no extra call or wait). The last line asks a
   small question back, as today.
4. After the answer: **"Got it?"** prompt (`thread_prompt` mode) with two buttons, **Got it** and **Ask another**,
   plus the mic (a spoken "yes / got it" is routed as `continue`; a spoken question is a follow-up).
   - Got it → `POST /api/threads/{id}/reply {understood: true}` → thread `resolved`; play `recap` if returned; resume.
   - Ask another → `reply {understood: false}` → returns `line` ("Sure, which part is still unclear? Ask me.") and
     the mic opens; the next question carries `thread_id` (a follow-up).
   - No response for `policy.idle_ms` → `POST /api/threads/{id}/abandon` → `abandoned`; play recap if any; resume.
5. **Limits**: a thread allows `max_followups` follow-ups after its first doubt (`can_follow_up = turns - 1 <
   max_followups`). A question on a thread that can't take more is not answered: the thread becomes `capped` and
   the server returns `line` ("Let's continue with the lecture; ask me again at the end and we'll go through it.")
   plus the recap. The "Ask another" button is hidden when `can_follow_up` is false.
6. **Recap** when a thread ends (resolved, capped or abandoned) and `turns >= recap_after_turns` or
   `audio_ms >= recap_after_seconds * 1000`. One or two sentences, 5–10 s spoken, from the current chapter and the
   last minute of transcript: "So, we were looking at free fall, and the teacher was just explaining why the packet
   starts with the balloon's velocity. Let's continue." LLM with retries; on failure a template line:
   "Okay, back to {chapter title}." A single short doubt gets no recap.
7. **Resume** at `resumePointMs(at_ms) = max(chapter start, at_ms - rewind_ms, 0)`, with a fade-in.
8. **Turns** count only answerable doubts (`now` / `chapter_end`); unclear/off-topic/clarify replies don't use up a
   turn. `audio_ms` adds the duration of every answer line delivered in the thread (server-side, when the answer is
   stored).

### 1.2 Checkpoints
1. Reaching an unanswered checkpoint's `at_ms` during playback pauses the video (`dueCheckpoint`). Play `intro`.
2. Each question: play its `say`, show the card. Correct → feedback line, next question. Wrong first pick → hint
   `why_wrong[picked]`, one retry. Wrong retry → reveal the answer, then re-teach: replay the teacher's
   `replay` range of the video, then the `reteach` clips on the overlay board, then `followup` with the mic open
   (questions asked then carry `checkpoint_idx`/`question_idx` and are routed as `confused`).
3. Skip (button) records `kind: "skip"`. After the last question play a bridge line and resume at `at_ms` (no
   rewind; checkpoints are placed in a silence).
4. **Seeking**: back anywhere; forward stops at the first not-done checkpoint passed (`clampSeek`) and asks it.
   Done checkpoints (every question answered first/skipped) are not asked again on rewatch.
5. No twin questions in video mode (v1).

### 1.3 Resume
`POST /api/sessions/{id}/video/position` every 5 s while playing and at every pause. Resume restores answered
checkpoint questions and earlier doubts (`GET /api/sessions/{id}/video/state`). The resume card offers
"Continue from mm:ss".

## 2. Backend

### 2.1 Prep (`app/prep/video.py`, `python -m app.prep.video`)  — agent B1
```
python -m app.prep.video "../input_audio/Lecture 5 Original.MP4" --slug lecture-05-video --language english \
    --lecture-language english --voice lecture5-sarvam --share-from lecture-05-en [--label TEXT] [--redo STAGE]
```
Stages cached as JSON in `data/lessons/<slug>/` like `app.prep` (same `cached()`/`--redo` behaviour):
1. `video`: ffmpeg → `data/media/videos/<slug>.mp4`, H.264 (libx264, crf 26, preset veryfast), max 1280×720 keeping
   aspect, AAC 96k, `-movflags +faststart`. Cache records `{url, duration_ms}` (ffprobe). Skip if the file exists.
2. `transcript`: `--share-from` copies `transcript.json` (and nothing else); otherwise reuse `make_transcript`.
3. `chapters`: LLM (`llm.prep_json`, `CHAPTERS_SYSTEM`) on the `[mm:ss]` transcript → topics and 4–10 subtopics
   (`topic, title, summary, key_terms, misconceptions, start_s, end_s`). Code validates: sorted, contiguous
   (each start = previous end, first = 0, last = duration), no zero-length; fixes small gaps/overlaps by snapping.
4. `checkpoints`: LLM (`CHECKPOINTS_SYSTEM`) gets the chapters + transcript and decides **where** (after which
   chapters) and **how many** questions (1..`checkpoint_max_questions`, more for denser chapters), with the reason.
   Code enforces: ≥ `checkpoint_min_gap_s` apart, ≤ `checkpoint_max`, never in the first 60 s, the last one may be at
   the end. `at_ms` = the chapter end snapped to the middle of the nearest silence within
   `checkpoint_snap_window_s` (ffmpeg `silencedetect=noise=-30dB:d=0.4` on the audio), else the chapter end.
   Then the questions per checkpoint (`CheckQuestion` shape: question, say, options (4), correct_index, concept,
   explanation) about that chapter only.
5. `review`: fact-check every question against the transcript (reuse `REVIEW_SYSTEM`'s idea; corrected questions
   replace the drafts).
6. `remedy`: per question `why_wrong` hints (by option index, answer never leaked; reuse `leaks_answer`), 2–3
   `reteach` steps (`{say, draw}`, DRAW_SPEC, may use `motion`), `replay` = the chapter's range trimmed to ≤ 90 s
   around where the concept is explained (LLM gives `replay_from_s`/`replay_to_s`, code clamps to the chapter).
7. `audio`: TTS (lesson voice, `speak`) for intros, question `say`, hints, reteach steps, followups.
8. Publish with the existing `lesson_store.publish_video_lesson(db, slug, title, transcript, video_url, duration_ms,
   chapters, checkpoints, voice, language, label) -> Lesson` (new version; `kind="video"`, no scenes).
   Fillers/bridges via the existing `make_fillers`.
Prompts go in `app/prompts.py` under "Video lessons: prep". English lesson: use `for_gender(..., language)`.
Options are shuffled with `quiz.shuffle_checks` per checkpoint (seeded by slug + idx).

### 2.2 Answer context (`app/context/`) — agent B2
- `base.py`: `class Passage(BaseModel): source: str; ref: str; text: str; score: float = 0.0`;
  `@dataclass(frozen=True) class ContextQuery: lesson_id: int; question: str; at_ms: int | None = None;
  chapter_idx: int | None = None`; `class ContextSource(Protocol): name: str; async def retrieve(self, q:
  ContextQuery, k: int) -> list[Passage]`.
- `transcript.py`: `@dataclass(frozen=True) class Segment: start_ms: int; end_ms: int; text: str`;
  `parse_transcript(text: str, total_ms: int | None = None) -> list[Segment]` (lines `[mm:ss] text`, also
  `[h:mm:ss]`; a segment ends where the next starts, the last at `total_ms` or start + 30 000; lines without a mark
  join the previous segment); `window(segments, at_ms, window_ms) -> list[Segment]` (segments overlapping
  `[at_ms - window_ms, at_ms]`); `keyword_matches(segments, question, k) -> list[tuple[Segment, float]]` (score =
  overlap of lower-cased word sets minus stop words, ties by time, zero scores dropped);
  `class TranscriptSource` (`name = "transcript"`, reads the lesson's transcript and chapters from the DB with
  `SessionLocal`; returns the window (score 1.0, source "transcript", ref "mm:ss–mm:ss"), then the chapter's
  segments, then keyword matches elsewhere).
- `documents.py`: `class DocumentSource` (`name = "documents"`): the plug point for notes/book PDFs. v1 returns
  `[]`; its docstring describes the future ingestion (PDF text + vision descriptions of figures → chunks with
  embeddings in a `document_chunks` table, per lesson) so it can be filled without touching callers.
- `service.py`: `SOURCES: dict[str, Callable[[], ContextSource]]` registry; `get_context_service() ->
  ContextService` from `settings.context_sources` (unknown name → `ValueError`);
  `ContextService(sources, max_chars).gather(q, k_per_source=6) -> list[Passage]` (all sources concurrently; a
  failing source is logged with `log_event("context.source_failed", ...)` and skipped; dedupe by (source, ref);
  transcript window first, then by score; cut to `max_chars` total text); `render(passages) -> str` (blocks
  `[transcript 12:40–13:10] text`, blank line between).

### 2.3 Doubt threads (`app/services/threads.py`, `app/api/video_doubts.py`, `app/api/questions.py`) — agent B3
- `@dataclass(frozen=True) class ThreadPolicy: max_followups: int; recap_after_turns: int; recap_after_seconds:
  int` with `@classmethod from_settings()`; `can_follow_up(turns, policy) -> bool`; `needs_recap(turns, audio_ms,
  policy) -> bool` (rules in 1.1); `thread_out(thread, policy, recap=None, line=None) -> ThreadOut`.
- DB helpers: `open_thread(db, sess, at_ms, chapter_idx) -> DoubtThread`; `get_open_thread(db, sess, thread_id)`;
  `async close_thread(db, thread, status, lesson) -> ThreadOut` (sets status/ended_at, recap when needed — LLM
  `RECAP_SYSTEM` via `with_retries`, template fallback; synthesized with the lesson voice; logged `thread.closed`
  with turns/audio_ms/recap).
- `POST /api/sessions/{id}/video/questions` (multipart: `audio`, `at_ms`, optional `thread_id`, optional
  `checkpoint_idx` + `question_idx`) → `VideoQuestionOut`. Uses `app.audio.transcribe` (STT with retries), a video
  routing prompt (`VIDEO_CLASSIFY_SYSTEM`: chapters with times and key terms instead of scenes, current chapter,
  transcript of the last 60 s, the open thread's earlier doubts; `target_scene_idx` = target chapter index and is
  validated to be ahead), clarify flow (confidence < 0.6, "yes" replays pending) as today. A `Doubt` row with
  `scene_idx = chapter_idx`, `step_idx = 0`, `at_ms`, `thread_id`, `turn`. A follow-up on a thread that can't take
  more → `capped` (see 1.1.5), `answer_when = "none"`. A `continue` intent on an open thread closes it as
  `resolved` and returns the recap in `thread`. Mastery: struggle intents lower the chapter's concept like today.
- `POST /api/threads/{id}/reply {understood}` → `ThreadOut`; `POST /api/threads/{id}/abandon` → `ThreadOut`.
  Replies to a thread that is not open return it unchanged (idempotent).
- `GET /api/doubts/{id}/answer`: for video lessons the prompt is built with `get_context_service()` (passages
  rendered, with refs) instead of the whole transcript, and `VIDEO_ANSWER_SYSTEM` (framing first line, 3–4 lines,
  English, DRAW_SPEC, answer only from the given context; if not in it, say it isn't covered in this lecture and
  give one line of intuition). After storing the answer, add its total `duration_ms` to the thread's `audio_ms`.
  Scene lessons keep the current prompt.
- Fixed lines (cached audio, `speak_cached` + `line_for`): thread "unclear part?" line, capped line, abandon line.

### 2.4 Video session API (`app/api/video.py`, `app/api/lessons.py`, `app/api/sessions.py`) — agent B5
- `lessons.py`: `lesson_out` fills `kind`, `video`, `chapters`, `checkpoints` (`CheckpointOut` from the stored
  dicts), `policy` (from settings; `idle_ms = video_thread_idle_s * 1000`) for video lessons; `list_lessons`
  fills `kind` and `scene_count` = chapter count for video lessons.
- `POST /api/sessions/{id}/video/position {at_ms}` → `{"ok": true}` (stores `position_ms`, touches the session).
- `POST /api/sessions/{id}/checkpoints/{idx}/answer {question_idx, chosen_index, kind}` → `CheckOut` (404 unknown
  checkpoint/question; 409 a second `first` answer; `retry` only after a wrong `first`; mastery updated on `first`
  like scene checks; feedback from `CHECK_LINES`).
- `POST /api/sessions/{id}/checkpoints/{idx}/skip {question_idx}` → `{"ok": true}` (kind "skip", idempotent).
- `GET /api/sessions/{id}/video/state` → `VideoStateOut`.
- `sessions.py`: `resume`/`progress`/`history`/`summary` handle video lessons: resume gives `position_ms` (scene_idx
  = chapter at that time, step_idx 0); percent = position / duration; checks = checkpoint answers (first answers
  correct / questions answered); summary uses checkpoint answers and the session's doubts.

### 2.5 Telemetry and retries (`app/telemetry.py` exists) — agent B4
- Apply `with_retries` inside `llm.complete_json` (per API call, `settings.llm_retries`), `llm.stream_lines`
  (only around opening the stream: once a line was yielded, a failure is logged and ends the stream), `audio.speak`
  (`tts_retries`), and add `async def transcribe(wav: bytes, **context) -> str` to `app/audio.py`
  (`stt_retries`, uses `get_stt()` and `current_stt_language`). Retried errors: timeouts, connection errors, 429 and
  5xx (`openai.APIConnectionError`, `APITimeoutError`, `RateLimitError`, `InternalServerError`; httpx
  equivalents in providers); never 4xx validation errors.
- Log timing events: `stt.done`, `route.done`, `answer.first_line`, `answer.done`, `tts.done` with `ms`.
- `POST /api/events` (`ClientEventIn`) → `{"ok": true}` (stored via `log_event`); `GET
  /api/sessions/{id}/events?level=` → list of events (debug).
- JSON log formatting for the `tutor.events` logger configured in `main.py`; `log_event`'s DB write must not block
  the event loop for long (use `asyncio.to_thread` from async callers, or keep it as is if measured < 5 ms).
- Frontend: `api.clientEvent` from an axios response interceptor (status ≥ 500 or network error), `window.onerror`
  and `unhandledrejection`, and the video tutor's own failures (B4 adds a small `src/lib/telemetry.ts` with
  `reportError(kind, error, ctx)` that the engine imports).

## 3. Frontend

### 3.1 Engine (`src/engine/video/`) — agent F1
- `checkpoints.ts` (pure, signatures fixed): `doneCheckpoints`, `dueCheckpoint`, `clampSeek`, `chapterAt`,
  `resumePointMs` (rules in 1.1.7 and 1.2).
- `videoTutor.ts` (`VideoTutor`, methods fixed): owns the `<video>` element and a second `<audio>` for tutor
  speech; writes `store/video` and the overlay board into `store/player` (`drawn`, `active`, `clipTime`, `layer:
  'doubt'`, `caption`), reusing `schedule`/`layoutStep` from `engine/tutor.ts` and `streamAnswer`. Implements 1.1–1.3:
  pausing with fade, ack → prefetch answer → play lines on the overlay, thread prompt with idle timer, follow-ups,
  capped/recap/abandon, `chapter_end` and `target_chapter` deliveries (time watcher on `timeupdate` + rAF),
  checkpoints, replay of video ranges, resume, position saving, and error handling (a failed call shows an error
  line, logs via `reportError`, and resumes the video instead of hanging).
- Tests: `frontend/tests/video-checkpoints.test.ts` (written by the test author, don't edit).

### 3.2 UI (`src/pages/VideoLessonPage.tsx`, `src/components/video/*`) — agent F2
- `App.tsx`: pick `VideoLessonPage` for `kind === 'video'` (the home page passes the lesson summary's kind).
- `VideoLessonPage`: header like `LessonPage` (title, chapter "n/N: title", tags, progress), the video area
  (`VideoPlayer` + overlay), caption bar, side panel (mic via `MicButton` with `state` from `store/video`, pause,
  chapter list with times, doubts list), start/resume card ("Continue from mm:ss"), summary at the end.
- `VideoPlayer`: `<video playsInline>` attached with `tutor.attach`, custom seek bar with checkpoint markers and
  chapter ticks (seeking calls `tutor.seek`), no native controls.
- Overlay: when `store/video.overlay`, the paused video stays visible dimmed and the board (`Board` or `SkiaBoard`
  by `lesson.renderer`) shows over it, with a "Doubt" badge.
- `ThreadPrompt`: in `thread_prompt` mode, "Got it?" with **Got it** and **Ask another** (hidden when
  `!thread.can_follow_up`) and the idle countdown.
- `CheckpointCard`: question n of N, options, wrong picks marked, hint/explanation, Skip, Next/Continue — calls
  `tutor.answerCheckpoint`, `skipCheckpointQuestion`, `continueAfterCheckpoint`.
- UI strings via `lib/i18n.ts` (add English and Hinglish entries).

## 4. Edge cases (all agents)
- STT returns empty / noise → `unclear`, no turn used, thread stays open.
- Network or LLM failure while answering → log, play the fixed "sorry, let's continue" line, close the thread as
  `abandoned`, resume. Never leave the video paused with no way forward.
- A second mic press while a question is routing is ignored; the mic is disabled during checkpoint questions
  except at the follow-up step.
- A doubt asked during the recap or a bridge line cancels it and starts a follow-up only if the thread is still
  open; otherwise a new thread.
- `chapter_end`/`target_chapter` deliveries that come due during a thread or checkpoint wait until it ends.
- Seeking backward past a promised callback re-arms it; seeking forward past a `chapter_end` answer delivers it at
  the next pause point.
- Video ends: deliver pending answers, then the summary.
- Browser autoplay: playback starts from the Start button (user gesture); the tutor audio element is unlocked then.
- Old (generated) lessons are untouched; `kind` defaults to `"generated"`.

## 5. Tests
Written first by the test author from this spec, then locked in `docs/plan/tests.lock`:
- backend `tests/test_video_threads.py` (ThreadPolicy rules; reply/abandon/capped/continue flows through the API
  with the conftest fakes; recap present only when needed; idempotent replies),
  `tests/test_video_context.py` (parse_transcript, window, keyword_matches, ContextService ordering/budget/failing
  source), `tests/test_video_api.py` (lesson out for a published video lesson, position, checkpoint answer/skip
  rules and 404/409, video state, resume position),
  `tests/test_video_prep.py` (pure helpers B1 exposes: `fix_chapters(chapters, duration_s)`,
  `plan_checkpoints(plan, chapters, duration_s, min_gap_s, max_n, max_q)`, `snap_to_silence(t_s, silences,
  window_s)` where `silences` is a list of `(start_s, end_s)`).
- frontend `tests/video-checkpoints.test.ts`.
