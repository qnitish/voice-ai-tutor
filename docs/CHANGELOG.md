# Changelog

What was built each day, newest first, with the branch, the main commits and the design doc to read before changing
that area. Read `CLAUDE.md` for the architecture and `docs/plan/voice-tutor-decisions.md` for why the flow is the way
it is. Tests are written from the specs in `docs/plan/` and locked in `docs/plan/tests.lock`. Change a locked test only
when the spec rule itself changes, and say so in the commit.

Design docs:
- `docs/plan/voice-tutor-decisions.md`: the original decisions (scene lessons, doubt routing, providers).
- `docs/plan/improvements-spec.md`: interruption, guest progress, routing, quiz, remediation (sections 9–11).
- `docs/plan/ai-quality-and-improvements.md`: proposal for evaluating content quality (not built).
- `docs/plan/video-lesson-spec.md`: video lesson mode (threads, checkpoints, recap, retries, logging).

---

## 2026-10-28 (evening): Promised video doubts are answered after their chapter (branch `feature/video-lesson`)

- A covered_later doubt used to get only "You asked earlier…, let's look at that now" at the target chapter's start,
  with no answer. Now the player prefetches its answer from the chapter's start (`target_ms`) and, at the chapter's
  end or its checkpoint if that comes first (`deliver_ms`, `video_doubts.deliver_ms`), pauses, calls
  `POST /api/doubts/{id}/deliver` (the doubt moves to a new open thread as its answered turn), plays the callback and
  the answer on the board, then "Got it?" with follow-ups. Answered before the checkpoint quiz; not again after a seek back.
- The answer prompt for it ("Answered later") uses the target chapter as watched, skips the framing line and connects
  the answer to what was just shown. The routing prompt's callback ends "we just covered this, so let me connect it
  to your question."
- Spec `video-lesson-spec.md` 1.1.2 updated for this.

---

## 2026-10-28 (later): Textbook pages in video doubt answers (branch `feature/video-lesson`)

- `python -m app.prep.book` renders chosen pages of the class textbook (JPEG, about 1.15 MP, the size Claude reads
  without scaling) to `data/lessons/<slug>/book/` with `book.json`. `lecture-05-video`: Sears and Zemansky ch. 2,
  printed pp. 48, 51–54 (constant-acceleration equations, 2.5 Freely Falling Bodies, Examples 2.6–2.8).
- Every video answer gets them as images before the doubt (`app/context/book.py`), marked as a cached prefix after the
  system prompt; message parts (text, image) in `llm_base.Messages`, converted by the Anthropic and OpenAI providers.
  `CONTEXT_BOOK_PAGES=false` switches them off. The Anthropic provider logs `llm.usage` (input, cache read/write, output).
- `VIDEO_ANSWER_SYSTEM`: the book corrects definitions, values and sign conventions and answers what the lecture
  doesn't cover; the teacher's notation, examples and level come first; the hidden `source` line ends with "Book p.N".
- `scripts/book_context_eval.py`: five lecture-5 doubts answered with and without the pages (two runs on
  2026-10-28: about 7.6k more input tokens per answer, read from the cache after the first; the first spoken line
  about 1–1.4 s later on average).

---

## 2026-10-28: Follow-ups continue on the board; recap over the paused video, in first person (branch `feature/video-lesson`)

- A question asked while a doubt is being answered is a follow-up: its answer is drawn below the earlier one on the
  same board (`engine/followup.ts`: each answer laid out in its own `Segment`, stacked under a dashed separator; the
  board lifts with an eased scroll, `boardScroll` in the player store, both renderers). No filler, ack or bridge in
  between; the answer prompt continues from what is on the board instead of starting over.
  - Video lessons: follow-ups in an open thread stack the same way; answered-now follow-ups get no ack (except
    covered-later answered early, `VIDEO_ACK_COVERED_NOW`).
  - Whiteboard lessons: new `doubts.follow_up_of`; the mic also works during "now" answers; routing and the answer
    prompt get the follow-up chain; a cut-off line is said again when the follow-up has nothing to answer.
- Video recap: when a thread ends, the board closes and the recap plays over the paused lecture at the resume point;
  the video starts only after it. Recap, answers and the covered-later ack speak as the teacher ("I", "we", "the next
  section", the section's name) instead of "the teacher".

---

## 2026-10-27 (evening): Doubt handling from the first test sessions; seek bar never stuck (branch `feature/video-lesson`)

From a review of the 19 doubts asked in the afternoon's test sessions:
- New intents `reply` (the student answers the tutor's closing question or reacts to the answer: judged or
  acknowledged in 1-2 lines, no re-explaining, uses no follow-up turn and is never capped) and `overview` ("what does
  this lecture cover / what have we done so far", answered from the chapter list).
- `doubts.said` keeps what STT heard; the answer prompt gets it next to the rewrite. A rewrite that describes the
  student ("The student's doubt is unclear…") is replaced by the heard text.
- Answer prompt: the chapter list (watched / playing / not watched yet), the turn in the pause, the tutor's last line;
  transcript passages after the pause are marked so earlier material is preferred. The framing line is only for the
  first doubt of a pause and is one clause; covered_later needs a later chapter that names the topic.
- Player: the seek bar works at the "Got it?" prompt (ends the thread) and on a waiting checkpoint card (a seek back
  leaves the checkpoint until the video reaches it again). Watchdogs end a stalled clip (`Speech.play`), a stalled
  answer stream (30 s without a line) and hung video API calls (timeouts), so a thread can't freeze the player.

---

## 2026-10-27 (later): Pluggable LLM provider, Claude by default; checkpoint questions v2 (branch `feature/video-lesson`)

- Every LLM call now goes through `providers.get_llm()` (`LLM_PROVIDER`: `anthropic`, `azure`, `openai`, `deepseek`,
  `gemini`), the same pattern as STT/TTS. Default: Claude Opus 5.5 for answers (effort low) and prep (effort high),
  Claude Haiku 5.5 for doubt routing. Azure credentials were removed from `.env`.
- Video checkpoints ask about the whole section since the previous pause (`from_chapter`, `prep/video.section`), not
  only its last chapter; the question and review prompts reject "what happens next" / "advantage of the method"
  recall questions. `lecture-05-video` republished as a new version with the regenerated questions.

---

## 2026-10-27: Video lesson mode (branch `feature/video-lesson`, not merged to main)

Spec: `docs/plan/video-lesson-spec.md`. Built in parallel by agents in worktrees, each piece merged on the branch.

**What it does:**
- Plays the real lecture video as is, split into chapters.
- The student can pause with the mic at any time and ask a doubt. It is answered in the teacher's cloned voice, from
  the transcript around that point, and drawn on a board over the paused video.
- After an answer the tutor asks "Got it?" and allows up to 3 follow-ups. After a long doubt session it plays a short
  recap, then resumes the video 3 s before where it was paused.
- Checkpoint questions pause the video after topics. The flow is: question, then hint, then one retry, then the
  answer, then the teacher's clip replayed, then the mic opens for follow-ups.
- The student can seek back freely. Seeking forward stops at an unanswered checkpoint.

**Backend:**
- New tables: `chapters`, `checkpoints`, `doubt_threads`, `checkpoint_answers`, `session_events`.
- New column: `lessons.kind` (`generated` | `video`).
- `app/prep/video.py` turns a video into a lesson in stages: transcript, chapters, checkpoints, review, remedy, audio,
  fixed lines.
  - Run with `--share-from` to reuse another lesson's transcript.
  - `--lines-only` regenerates only the fixed spoken lines of a published lesson.
- `app/api/video_doubts.py`: routes a doubt and manages its thread.
  - "Every question gets a thread"; the routing rules are in spec 1.1.2.
  - `app/services/threads.py` holds the follow-up cap, the recap rules and closing a thread.
- `app/api/video.py`: the lesson, position, checkpoint answers, state, resume and summary.
- `app/context/`: pluggable answer context.
  - `transcript` is built now; `documents` is a stub for notes and book PDFs later.
  - Choose sources with `CONTEXT_SOURCES`.
- `app/telemetry.py`: one JSON log line per event, also stored per session.
  - `GET /api/sessions/{id}/events` shows where a flow failed.
  - `with_retries` retries LLM, TTS and STT calls (`*_RETRIES` settings).
- All limits are in `config.py` under "Video lessons": follow-ups, recap, rewind, idle, checkpoint planning, context
  and retries.
- Rule: never hold a DB session or row lock across an LLM or TTS await. Commit first. Breaking this caused a server
  hang (see 4e08e35 and 2aec879).

**Frontend:**
- `pages/VideoLessonPage.tsx`, with `components/video/*`.
- `engine/video/`:
  - `videoTutor.ts`: the play order is ack, then answer, then the thread line, then recap, then resume.
  - `checkpoints.ts`, `speech.ts`, `doubts.ts`.
- `store/video.ts` and `lib/telemetry.ts` (reports client errors).
- Layout: "Your doubts" sits under the mic in its own auto-scrolling box. Chapters are collapsible and closed by
  default.

**Fixes from the final review (2aec879):**
- A capped doubt gets a neutral ack.
- An empty mic tap resumes the interrupted answer.
- The mic is closed at checkpoint start.
- Safari audio is unlocked in the Start click.
- Prefetched answers are aborted when they won't play.
- Recaps run as one task per thread.
- Resume ignores older sessions after "Start over".
- Rule change: `unclear` always keeps the thread open, so "Could you say that again?" waits for the student. Both the
  spec and its test were updated for this.

**Data:** published lesson `lecture-05-video` v1:
- Source video `data/media/videos/lecture-05-video.mp4`.
- English, voice `lecture5-sarvam`.
- 8 chapters and 4 checkpoints (10 questions).
- Some questions at checkpoint 3 are weak; fix them with `--redo checkpoints`.

**Tests:** 552 backend and 172 frontend.

**Known limits:**
- Promised "covered later" callbacks are lost when a session is resumed.
- `reteach` is empty in v1.
- Safari is not tested on a device.

## 2026-10-27: Motion action and Skia renderer (branch `feature/motion-skia`, commit 369eef6)

- New `motion` draw action (`frontend/src/engine/motion.ts`): objects move by the equations of motion. It supports
  velocity, acceleration, landing on the ground, a trail, strobe ghosts and a live velocity arrow.
- Board drawing goes through one shared frame list (`engine/frame.ts`). Both the SVG `Board.tsx` and a new CanvasKit
  `SkiaBoard.tsx` draw from it.
- Each lesson picks its renderer in `lessons.renderer` (prep `--renderer svg|skia`). The fonts are in
  `frontend/public/fonts`.
- Demo lessons `free-fall-en-svg` and `free-fall-en-skia`; their draw data was written by hand.
- `feature/video-lesson` is built on top of this branch.

## 2026-10-26: Sarvam and cloned voices (main, 0de07e8)

- `providers/sarvam.py`: Saaras STT, plus Bulbul and cloned-voice TTS, with retries.
- TTS is chosen by voice: a Sarvam clone goes to Sarvam, a Gnani embedding to Gnani.
- `python -m app.voices clone --provider sarvam` (use only with the speaker's permission).
- `PAUSE_RULES`: pacing comes from punctuation only.
- `scripts/voice_samples.py` writes version-tagged sample clips.
- `lessons.label` and prep `--label`. Lessons show tags (language, voice, `vN`, label).

## 2026-10-23: Drawing improvements (main, dfbbc4c)

- Whiteboard blocks and the layout engine (`engine/layout.ts`): overlapping text moves to a free line, and labels are
  placed where they are clear.
- Fonts are loaded before playback starts. See CLAUDE.md, "Prompts" and "Frontend playback".

## 2026-10-22: Remediation, voices, content quality (main, 211737f, e7703b4)

- After a wrong check answer: a hint for the picked option, one retry, a re-teach on the doubt board, then the voice
  follow-up. A twin question comes after the next scene. Spec sections 9–11.
- Each lesson has its own voice (stock or cloned). A male voice gets male verbs.
- Prep stage `review` fact-checks the check questions, and prep has its own model setting.
- Answers are grounded in the lecture. New `continue` intent.
- A resumed session keeps the earlier doubts (`engine/restore.ts`).

## 2026-10-21: First version (main, bc5e24f, 4377c79)

- Offline prep from audio to a narrated whiteboard lesson: transcript, then outline, then scenes, then audio.
- Live push-to-talk doubts with routing (later, end of scene, now, redirect) and NDJSON streamed answers.
- Guests, sessions, resume, check questions and progress. Spec: `docs/plan/improvements-spec.md`.
