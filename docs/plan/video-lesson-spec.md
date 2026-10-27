# Video lesson spec (demo: Lecture 5, English)

The recorded lecture video plays as is. The student can interrupt at any time with a doubt; the tutor answers in the
teacher's cloned voice on a whiteboard overlay, grounded in the lecture (transcript now; notes and book PDFs later),
asks "Got it?", takes follow-ups, and recaps a long doubt before the video resumes. At checkpoints the video pauses
for 1–3 questions. Generated (scene) lessons keep working unchanged: a lesson has `kind` `"generated"` or `"video"`.
The demo is English only: video fixed lines are English literals (no `line_for`/`ENGLISH_LINES`).

Shared contracts are committed; build against them and don't change them (say so in your report if one is wrong):
`backend/app/db.py` (Chapter, Checkpoint, DoubtThread, CheckpointAnswer, SessionEvent, new columns),
`backend/app/schemas.py` ("Video lessons" section, LessonOut/LessonSummaryOut `kind`, ResumeOut `position_ms`),
`backend/app/config.py` ("Video lessons" settings), `backend/app/telemetry.py` (keep `log_event`'s sync signature),
`backend/app/lesson_store.py` (`publish_video_lesson`), `backend/app/audio.py` (`transcribe`),
`backend/app/context/{base,service}.py` (signatures), `frontend/src/lib/{types,api,telemetry,i18n}.ts`,
`frontend/src/store/video.ts` (incl. `micState`), `frontend/src/engine/host.ts`, and the skeletons
`frontend/src/engine/video/{checkpoints,videoTutor}.ts`. `sessions.py` helpers imported by `questions.py`
(`get_session`, `touch`, `update_mastery`, `callback_path`, `callback_spoken`) keep their names and signatures.

## 1. Student flow

```
video plays ── mic ──► video pauses ──► doubt thread ──► (recap) ──► video resumes at resumePointMs
video plays ── checkpoint at_ms reached ──► video pauses ──► intro + 1..N questions ──► video resumes at at_ms
```

### 1.1 Doubt thread
1. Mic pressed: the video pauses (audio fade like the scene player). Recording, STT, routing.
2. Every question gets a thread. A question without a `thread_id`, or whose `thread_id` names a thread that isn't
   open, creates a new thread (never a 409). The server returns a fixed **ack** (cached audio) and routes:

   | intent | answer_when | thread after routing | player does |
   |---|---|---|---|
   | prerequisite, confused, not_covered | `now` | open, `turns += 1` | play ack, stream the answer on the overlay, then "Got it?" |
   | covered_later (only when no thread is open) | `target_chapter` | closed `resolved` | play ack, resume; at `target_ms` play the callback |
   | covered_later inside an open thread | `now` | open, `turns += 1` | as `now` (the student is already paused) |
   | repeat | `replay` | closed `resolved` | play ack, seek to `replay_from_ms = max(chapter.start_ms, at_ms - 30000)`, play |
   | continue | `none` | closed `resolved` (if one was open) | play ack, recap if returned, resume |
   | off_topic, unclear | `none` | stays open if it was open (no turn used), else closed `resolved` | play ack; if still open show "Got it?" again, else resume |

   Video routing never returns `clarify`: a low-confidence answerable doubt is answered `now` (the framing line
   restates the question). A spoken short "yes / got it" (`routing.is_affirmative`) on an open thread whose last
   doubt was answered is `continue` without an LLM call.
3. **Framing**: the answer's first spoken line acknowledges the doubt and places it in what is being studied, then
   leads in, e.g. "So you're asking why the packet first goes up. Right now we're studying motion under gravity, so
   let's see it with an example." It is part of the streamed answer (no extra call). The last line asks a small
   question back.
4. After the answer: **"Got it?"** (`thread_prompt`) with **Got it** and **Ask another** (hidden when
   `!thread.can_follow_up`) and the mic.
   - Got it → `POST /api/threads/{id}/reply {understood: true}` → `resolved`; play `recap` if any; resume.
   - Ask another → `reply {understood: false}`: if `can_follow_up`, the thread stays open and `line` = "Sure, which
     part is still unclear? Ask me." (`awaitingFollowup`, mic open); otherwise the thread is closed `capped` with
     the capped `line` and the recap.
   - No answer within `policy.idle_ms` (at the prompt or while awaiting a follow-up) → `POST
     /api/threads/{id}/abandon {reason: "idle"}` → `abandoned`, `line` = null; play recap if any; resume.
5. **Limits**: `turns` = answerable doubts in the thread (incremented at routing time when `answer_when == "now"`).
   `can_follow_up = turns - 1 < max_followups`. The cap is checked after classification, only for answerable
   intents: such a question on a thread with `turns - 1 >= max_followups` is not answered — the Doubt row is stored
   (classified intent, `answer_when = "none"`), `turns` is unchanged, the thread is closed `capped`, `thread.line` =
   "Let's continue with the lecture. Ask me again at the end and we'll go through it.", `thread.recap` as usual.
   `Doubt.turn` / `VideoQuestionOut.turn` = the thread's `turns` before this doubt.
6. **Recap** when a thread ends (resolved, capped or abandoned) and `turns >= recap_after_turns` or `audio_ms >=
   recap_after_seconds * 1000`: one or two sentences (5–10 s spoken) from the current chapter and the last minute of
   transcript — "So, we were looking at free fall, and the teacher was just explaining why the packet starts with the
   balloon's velocity. Let's continue." To avoid a wait at "Got it", it is generated in the background as soon as
   `needs_recap` becomes true (after an answer is stored) and kept on `thread.recap`; `close_thread` uses the stored
   one, else generates it, and on any failure or empty text uses the template "Okay, back to {chapter title}."
   A single short doubt gets no recap.
7. **Play order** for any `VideoQuestionOut` / `ThreadOut`: `ack` → `thread.line` → `thread.recap` → resume when
   `thread.status != "open"` and `answer_when != "now"`.
8. **Resume** at `resumePointMs(at_ms) = max(chapter start, at_ms - rewind_ms, 0)`, with a fade-in.
9. **Errors**: an answer stream that fails or yields zero lines → `abandon {reason: "error"}` → `line` = "Sorry,
   something went wrong there. Let's continue with the lecture." → resume. If that call fails too, show the
   `videoError` caption and resume silently.

### 1.2 Checkpoints
1. Reaching a not-done checkpoint's `at_ms` while playing pauses the video (`dueCheckpoint`; on `ended` the engine
   uses `nowMs = Infinity`). `start(fromMs)` asks a not-done checkpoint with `|at_ms - fromMs| < 500` right away.
2. Play `intro`. Each question: play its `say`, show the card. Correct → feedback line, next question. Wrong first
   pick → hint `why_wrong[picked]`, one retry. Wrong retry → reveal the answer, then replay the teacher's `replay`
   range of the video (the card hides while `stage === 'reteach'`), then any `reteach` clips on the overlay
   (none in v1 prep), then `followup` with the mic open. Questions asked then carry `checkpoint_idx`/`question_idx`,
   are forced to `confused` unless `off_topic`/`unclear`/`continue`, and their thread returns to the checkpoint card
   at the end (no recap, no video resume).
3. Skip records `kind: "skip"`. After the last question play a bridge line and resume at `at_ms` (no rewind).
4. **Seeking** (only while playing or paused): back anywhere; forward stops at exactly the first not-done
   checkpoint's `at_ms` passed (`clampSeek`), and the engine asks it. Done checkpoints are not asked again.
5. No twin questions in video mode.

### 1.3 Resume
`POST /api/sessions/{id}/video/position` every 5 s while playing and at every pause. The page fetches
`api.videoState` and passes it to `start`, which restores answered questions and earlier doubts. The resume card
offers "Continue from mm:ss". The engine sets `usePlayer.lesson` too (MicButton, Board and `t()` read it).

## 2. Backend

### 2.1 Prep (`app/prep/video.py`, `python -m app.prep.video`) — B1
```
python -m app.prep.video "../input_audio/Lecture 5 Original.MP4" --slug lecture-05-video --language english \
    --lecture-language english --voice lecture5-sarvam --share-from lecture-05-en [--label TEXT] [--redo STAGE]
```
Stages cached as JSON in `data/lessons/<slug>/` like `app.prep` (same `cached()`/`--redo` behaviour):
1. `video`: ffmpeg → `data/media/videos/<slug>.mp4` (H.264 libx264 crf 26 preset veryfast, ≤1280×720 keeping
   aspect, even dimensions, yuv420p, AAC 96k, `-movflags +faststart`); skip the encode if the file exists. Records
   `{url, duration_ms}` (ffprobe on the output). (`lecture-05-video.mp4` is already encoded.)
2. `transcript`: `--share-from` copies that slug's `transcript.json`; otherwise `make_transcript`.
3. `chapters`: LLM (`llm.prep_json`, `CHAPTERS_SYSTEM`) on the `[mm:ss]` transcript → 4–10 subtopics grouped in
   topics (`topic, title, summary, key_terms, misconceptions, start_s, end_s`), then `fix_chapters`.
4. `checkpoints`: LLM (`CHECKPOINTS_SYSTEM`) gets chapters + transcript and returns `{"plan": [{"after_chapter":
   int, "questions": int, "reason": str}]}` — where and how many (more for denser chapters); then
   `plan_checkpoints`, then `at_ms = round(1000 * min(snap_to_silence(t_s, silences, window), duration_s - 0.5))`
   with silences from `ffmpeg -af silencedetect=noise=-30dB:d=0.4` on the video. Then the questions per checkpoint
   (`CheckQuestion` shape, 4 options) about that chapter only, plus an `intro` line ("Let's check what we just
   covered: two quick questions."). `say` and hints never name option letters or positions (options are shuffled
   later).
5. `review`: fact-check every question against the transcript; corrected questions replace the drafts.
6. `remedy`: per question `why_wrong` hints by option index (answer never leaked; reuse `schemas.leaks_answer`, drop
   leaking hints) and `replay` (LLM gives `replay_from_s`/`replay_to_s` where the teacher explains the concept; code
   clamps to the chapter and to ≤ 90 s). `reteach` is `[]` in v1. A `followup` line ("Which part confused you? Ask
   me, or let's continue.").
7. `audio`: TTS (lesson voice, `speak`) for intros, question `say`, hints, followups.
8. Publish: shuffle all questions' options with one `quiz.shuffle_checks` call (seed = slug) and remap their
   `why_wrong`; `lesson_store.publish_video_lesson(...)`; fillers/bridges via `make_fillers(voice, language)`.

Pure helpers (tested):
- `fix_chapters(chapters: list[dict], duration_s: float) -> list[dict]`: dicts with `start_s`/`end_s`; sort by
  `start_s`; first `start_s = 0`; each `end_s` = next `start_s` (the LLM's `end_s` is ignored except the last); last
  `end_s = duration_s`; drop chapters with `end_s <= start_s`; `ValueError` if none remain. Other keys kept.
- `plan_checkpoints(plan: list[dict], chapters: list[dict], duration_s: float, min_gap_s: float, max_n: int, max_q:
  int) -> list[dict]`: output `{"chapter_idx", "t_s", "questions", "reason"}` sorted by `t_s`, `t_s` = that
  chapter's `end_s`; drop items with an invalid chapter index, duplicates of a chapter, or `t_s < 60`; clamp
  `questions` to 1..`max_q`; greedily in time order drop an item closer than `min_gap_s` to the previous kept one;
  truncate to `max_n`.
- `snap_to_silence(t_s: float, silences: list[tuple[float, float]], window_s: float) -> float`: the midpoint of the
  silence whose midpoint is nearest `t_s` with `|mid - t_s| <= window_s` (the earlier one on a tie), else `t_s`.
Prompts go in `app/prompts.py` under "Video lessons: prep".

### 2.2 Answer context (`app/context/`) — B2
- `base.py` (committed): `Passage`, `ContextQuery`, `ContextSource`.
- `transcript.py`:
  - `@dataclass(frozen=True) class Segment: start_ms: int; end_ms: int; text: str`.
  - `parse_transcript(text: str, total_ms: int | None = None) -> list[Segment]`: lines starting `[mm:ss]` or
    `[h:mm:ss]` (minutes may exceed 59, e.g. `[75:10]`); a segment ends where the next starts, the last at
    `total_ms` or start + 30 000; lines without a mark join the previous segment; text before the first mark starts
    at 0; text stripped; empty segments dropped.
  - `tokens(text) -> set[str]`: `re.findall(r"[a-z0-9]+", text.lower())` minus `STOP_WORDS` (a module constant:
    common English function words such as a, an, the, is, are, was, of, to, in, on, at, and, or, it, this, that, what,
    why, how, we, you, i, do, does, be, for, with, so, then, as, by, from).
  - `window(segments, at_ms, window_ms) -> list[Segment]`: segments with `start_ms <= at_ms and end_ms > at_ms -
    window_ms`.
  - `keyword_matches(segments, question, k) -> list[tuple[Segment, float]]`: score = `|tokens(q) ∩ tokens(seg)| /
    |tokens(q)|`; zero scores dropped; sorted by score desc, then time; top k.
  - `class TranscriptSource` (`name = "transcript"`): reads the lesson's transcript, `duration_ms` and chapters
    (`SessionLocal`); returns one merged window passage (score 3.0, ref = first start–last end as `mm:ss–mm:ss`),
    then the chapter's segments not in the window (score 2.0 each), then keyword matches not already included
    (their score); `k` limits the keyword matches.
- `documents.py`: `class DocumentSource` (`name = "documents"`), returns `[]`; its docstring is the plug-in guide
  (PDF text + vision descriptions of figures → chunks with embeddings in a `document_chunks` table per lesson;
  `retrieve` = embedding search) so it can be filled without touching callers.
- `service.py`: `SOURCES = {"transcript": TranscriptSource, "documents": DocumentSource}`; `get_context_service()`
  builds from `settings.context_sources` (unknown name → `ValueError`) with `settings.context_max_chars`;
  `ContextService.gather(q, k_per_source=6)`: all sources concurrently; a source that raises is logged
  (`log_event("context.source_failed", level="warn", source=…, error=…)`) and skipped; dedupe by `(source, ref)`;
  stable sort by score desc; keep whole passages in order until the next would push the total text over
  `max_chars` (the first passage alone is truncated to `max_chars`). `render(passages) -> str`: blocks
  `[{source} {ref}]\n{text}` joined by a blank line; `""` for none.

### 2.3 Doubt threads (`app/services/threads.py`, `app/api/video_doubts.py`, `app/api/questions.py`) — B3
- `@dataclass(frozen=True) class ThreadPolicy: max_followups: int; recap_after_turns: int; recap_after_seconds:
  int` + `@classmethod from_settings()`; `can_follow_up(turns: int, policy) -> bool`; `needs_recap(turns: int,
  audio_ms: int, policy) -> bool`; `thread_out(thread, policy, recap: Spoken | None = None, line: Spoken | None =
  None) -> ThreadOut` (recap defaults to the stored `thread.recap`).
- `open_thread(db, sess, at_ms, chapter_idx) -> DoubtThread`; `get_open_thread(db, sess, thread_id) ->
  DoubtThread | None`; `async close_thread(db, thread, status, lesson, line: Spoken | None = None) -> ThreadOut`
  (status/ended_at, recap rule 1.1.6, logs `thread.closed` with turns/audio_ms/recap); `async
  prepare_recap(thread_id)` (background: generates and stores the recap when `needs_recap`).
- B3 does not wrap `llm.*`/`speak` in `with_retries` (they retry inside, B4); it catches the final exception and
  falls back (template recap, fixed lines).
- `POST /api/sessions/{id}/video/questions` (multipart: `audio`, `at_ms`, optional `thread_id`, optional
  `checkpoint_idx` + `question_idx`) → `VideoQuestionOut`, per 1.1. STT via `audio.transcribe(wav,
  lesson.language, session_id=…)`, with the existing `max_seconds` → 400 and < 300 ms → `""` rules. Routing prompt
  `VIDEO_CLASSIFY_SYSTEM`: chapters with times and key terms (instead of scenes), current chapter, transcript of the
  last 60 s, the open thread's earlier doubts and answers; `target_scene_idx` = target chapter index, must be ahead
  of the current chapter, else the doubt is `not_covered`. Doubt row: `scene_idx = chapter_idx`, `step_idx = 0`,
  `at_ms`, `thread_id`, `turn`, `checkpoint_idx`, `question_idx`. Mastery for struggle intents: the `concept` of the
  first question of the checkpoint whose `chapter_idx` is the doubt's chapter (skip if none).
  Logs `route.done` (ms, intent, confidence).
- `POST /api/threads/{id}/reply` (`ThreadReplyIn`) → `ThreadOut`; `POST /api/threads/{id}/abandon`
  (`ThreadAbandonIn`, body optional) → `ThreadOut`. 404 unknown thread. A thread that isn't open is returned
  unchanged (idempotent).
- `GET /api/doubts/{id}/answer`: branch on `lesson.kind == "video"` **before** `answer_prompt` (which assumes
  scenes): prompt = student, doubt, intent, current chapter (title, summary), the thread's earlier doubts and
  answers, the checkpoint question and the student's picks when `checkpoint_idx` is set, and
  `render(await get_context_service().gather(ContextQuery(lesson.id, doubt.question, doubt.at_ms,
  doubt.scene_idx)))`; system `VIDEO_ANSWER_SYSTEM` (framing first line, 3–4 lines, English, DRAW_SPEC, answer only
  from the given context, else say it isn't covered in this lecture and give one line of intuition). When the answer
  is first stored (not on cached replays) add its total `duration_ms` to the thread's `audio_ms` and schedule
  `prepare_recap`. Logs `answer.first_line` and `answer.done` (ms, lines). Scene lessons keep the current prompt.
- Video fixed lines (English literals, `speak_cached`): ack per intent (reuse the meaning of `FIXED_ACKS`), "which
  part is still unclear", capped line, sorry line, plus prompts `VIDEO_CLASSIFY_SYSTEM`, `VIDEO_ANSWER_SYSTEM`,
  `RECAP_SYSTEM` — all in `app/prompts.py` under "Video lessons: live doubts".

### 2.4 Video session API (`app/api/video.py`, `app/api/lessons.py`, `app/api/sessions.py`) — B5
- `lessons.py`: `lesson_out` fills `kind`, `video`, `chapters`, `checkpoints` (`CheckpointOut` from stored dicts),
  `policy` (settings; `idle_ms = video_thread_idle_s * 1000`) for video lessons; `list_lessons` fills `kind` and
  `scene_count` = chapter count for video lessons.
- `POST /api/sessions/{id}/video/position {at_ms}` → `{"ok": true}`: 404 unknown session; `at_ms` clamped to the
  lesson's `duration_ms`; stores `position_ms`, touches the session.
- `POST /api/sessions/{id}/checkpoints/{idx}/answer {question_idx, chosen_index, kind}` → `CheckOut`: 404 unknown
  session/checkpoint/question; 422 `chosen_index >= len(options)`; 409 a second `first`, a `retry` without a wrong
  `first`, or a second `retry`. Mastery updated on `first` like scene checks (concept of the question). Feedback
  from `CHECK_LINES` (English via `line_for(..., "english")` is fine here since those lines exist in
  `ENGLISH_LINES`).
- `POST /api/sessions/{id}/checkpoints/{idx}/skip {question_idx}` → `{"ok": true}`: allowed anytime, idempotent.
- `GET /api/sessions/{id}/video/state` → `VideoStateOut` (`answered` from checkpoint_answers; doubts ordered by time,
  `answered = doubt.answer is not None`).
- `sessions.py`: resume/progress/history/summary handle video lessons. Resume gives `position_ms` (and `scene_idx` =
  the chapter at that time, `step_idx` 0); a video session with no saved position has no `position_ms` key (the
  serializer omits null). Percent = position / duration. Checks = checkpoint answers (correct first answers /
  questions with a first answer). Summary uses checkpoint answers and the session's doubts. Keep the helper names
  `questions.py` imports.

### 2.5 Telemetry and retries — B4
- `with_retries` inside `llm.complete_json` (each API call, `llm_retries`), `llm.stream_lines` (only opening the
  stream; after a line was yielded a failure is logged as `llm.stream_failed` and ends the stream), `audio.speak`
  (`tts_retries`), `audio.transcribe` (`stt_retries`; also logs `stt.done` with ms). Retry only timeouts,
  connection errors, 429 and 5xx (`openai.APIConnectionError`, `APITimeoutError`, `RateLimitError`,
  `InternalServerError`; the httpx equivalents raised by the providers), never 4xx/validation. `speak` logs
  `tts.done` (ms, chars).
- `POST /api/events` (`ClientEventIn`) → `{"ok": true}` (stored via `log_event`); `GET
  /api/sessions/{id}/events?level=` → `list[SessionEventOut]`, oldest first.
- JSON formatting for the `tutor.events` logger in `main.py`. `log_event` keeps its signature; its DB write may move
  to a background thread if needed.
- Frontend: append an axios response interceptor in `api.ts` below `http` (status ≥ 500 or network error →
  `reportError`, skipping `/events` URLs); `window.onerror` / `unhandledrejection` → `reportError` (installed in
  `main.tsx`).

## 3. Frontend

### 3.1 Engine (`src/engine/video/`) — F1
- `checkpoints.ts` (signatures fixed): `doneCheckpoints` (a checkpoint with 0 questions is done; otherwise every
  question has a `first` or `skip` answer), `dueCheckpoint` (`prevMs < at_ms <= nowMs`, first by time),
  `clampSeek` (returns exactly the checkpoint's `at_ms` when stopping there), `chapterAt`, `resumePointMs`.
- `videoTutor.ts` (`VideoTutor`, public methods fixed): owns the `<video>` and a second `<audio>` for tutor speech;
  writes `store/video` and the overlay board into `store/player` (`drawn`, `active`, `clipTime`, `layer: 'doubt'`,
  `caption`, `lesson`), reusing `schedule`/`layoutStep` and `streamAnswer`. Implements 1.1–1.3: pause with fade,
  play order (1.1.7), prefetching the answer stream as soon as the question is routed, thread prompt with the idle
  timer (`promptEndsAt`), follow-ups (`awaitingFollowup`), capped/recap/abandon/error, `target_chapter` callbacks
  (watch `timeupdate`; seeking back before `target_ms` re-arms a played callback), `replay`, checkpoints incl. the
  teacher replay, resume, position saving, `listening`/`thinking` flags, summary on end (`api.summary` →
  `store.summary`), `reportError` + `setTelemetrySession` for failures. Ignore a second mic press while routing.
- Tests: `frontend/tests/video-checkpoints.test.ts` (test author; don't edit).

### 3.2 UI — F2
- Files: `src/pages/VideoLessonPage.tsx`, `src/components/video/*`, `src/App.tsx`, `src/pages/HomePage.tsx`
  (`onStart(slug, kind)`; show a "Video" tag), i18n wording.
- `VideoLessonPage`: one `VideoTutor` per mount (StrictMode mounts twice; `stop()` is permanent), fetches the lesson,
  resume (`api.resume`) and `api.videoState`; header like `LessonPage` (title, "Chapter n/N: title", tags,
  progress); the video area (`VideoPlayer` + overlay); caption bar; side panel (`MicButton tutor={tutor}
  state={micState(store)}`, pause, chapter list with times (click = `tutor.seek`), doubts list); start/resume card
  ("Start lecture" / "Continue from mm:ss" — the click is the user gesture that allows playback); summary card at the
  end (reuse `SummaryCard` if it fits).
- `VideoPlayer`: `<video playsInline>` (no native controls) attached via `tutor.attach`; a seek bar with checkpoint
  markers and chapter ticks (calls `tutor.seek`).
- Overlay: when `overlay`, the paused video stays visible dimmed and the board (`Board` or lazy `SkiaBoard` by
  `lesson.renderer`) shows over it with a "Doubt" badge.
- `ThreadPrompt`: in `thread_prompt` (or `awaitingFollowup`), "Did that make sense?" with **Got it** / **Ask
  another** (hidden when `!thread.can_follow_up`) and the countdown from `promptEndsAt`.
- `CheckpointCard`: "Question n of N", options, wrong picks marked, hint/explanation, Skip, Continue; hidden while
  `stage === 'reteach'`.

## 4. Edge cases (all)
- Empty/noisy STT → `unclear`, no turn used.
- Any failure while answering → 1.1.9; never leave the video paused with no way forward.
- Second mic press while routing ignored; mic closed during checkpoint questions except the follow-up step.
- A doubt asked while the recap or a bridge plays cancels that audio; it continues the thread if still open, else
  starts a new one.
- A `target_chapter` callback that comes due during a thread or checkpoint waits until it ends.
- Video ends: finish any open thread, ask a not-done final checkpoint, then the summary.
- Generated lessons are untouched; `kind` defaults to `"generated"`.

## 5. Tests (test author, from this spec; locked in `docs/plan/tests.lock`)
Backend (conftest fakes; the fake LLM returns blank instances for unknown schemas, so blank recap text must fall
back to the template): `tests/test_video_threads.py` (ThreadPolicy rules; per-intent thread lifecycle (1.1.2);
reply/abandon/capped/continue/affirmative flows through the API; recap only when needed; idempotent replies; 404s),
`tests/test_video_context.py` (parse_transcript, tokens, window, keyword_matches, ContextService ordering, budget,
failing source, render), `tests/test_video_api.py` (lesson out for a published video lesson, list kind, position,
checkpoint answer/skip rules incl. 404/409/422, video state, resume `position_ms` present/absent),
`tests/test_video_prep.py` (`fix_chapters`, `plan_checkpoints`, `snap_to_silence` from `app.prep.video`).
Frontend: `tests/video-checkpoints.test.ts` (all five functions).
