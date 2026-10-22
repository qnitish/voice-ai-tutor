# Improvements spec: smooth interruption, guest progress, smarter routing, speed + quiz

Status: agreed scope (2026-10-21). This file is the **contract for the tests**. Tests are written from this file
only, without reading the implementation. The implementation must make them pass without editing them.

Out of scope for this round: content evaluation (`review` stage), math checks, review-at-session-start, re-teaching
after a wrong check answer (added later as section 9), streaming upload, a second TTS provider.

---

## What each change improves

| Change | Improvement for the student / team |
|---|---|
| Fade out / fade in around interruptions | No more voice cut mid-word; the pause sounds intentional. |
| Resume from the start of the sentence | The student hears the full sentence again instead of half a word. |
| Bridge line after an answer | A clear "back to the lesson" signal, so the board switch isn't confusing. |
| Questions during an answer join the same delivery | No lost or dangling doubts when the student asks twice. |
| Tap-to-talk with silence auto-stop | No need to hold the button; easier on phones and for kids. Holding still works. |
| `prerequisite` and `confused` intents, answered now | Basic gaps get explained right away instead of being dismissed as off-topic or delayed. |
| `repeat` replays the current step | "Phir se bolo" is instant, with no LLM call. |
| Previous doubts in routing context | Follow-ups like "aur woh wala?" are understood. |
| Ask back when unsure (`clarify`) | Fewer wrong answers caused by bad STT; the student confirms with "haan". |
| Lesson vocabulary in routing | Misheard technical words are corrected in the rewritten question. |
| Compact routing prompt + separate routing deployment | Smaller prompt = faster routing; can switch to a fast model with one env var. |
| Shuffled check options | Answer is no longer always option A; quizzes measure understanding. |
| Lesson versioning | Re-running prep no longer deletes students' sessions. |
| Resume position | Student can continue where they left off, with the board restored. |
| Progress, mastery, history APIs | Per-lesson %, scores, per-concept mastery and past doubts for each guest. |

---

## Test setup (applies to all backend tests)

- Location: `backend/tests/`. Run with `cd backend && .venv/bin/pytest`. `pytest` is the only extra dependency
  (`requirements-dev.txt`). No pytest-asyncio; use FastAPI's `TestClient` (sync).
- **Before importing anything from `app`**, set environment variables (e.g. at the top of `conftest.py`):
  - `DATABASE_URL=postgresql+psycopg://tutor:tutor@localhost:5440/voice_tutor_test` (dedicated test DB, already
    created; never use `voice_tutor`).
  - `DATA_DIR=<a temporary directory>` (media files are written under `$DATA_DIR/media`).
  - Azure keys are read from the root `.env`; tests must never reach the network.
- Schema reset: `from app.db import Base, engine`; `Base.metadata.drop_all(engine); Base.metadata.create_all(engine)`.
  Reset between tests (or truncate all tables) so tests are independent.
- App: `from app.main import app`; `TestClient(app)`.
- `ffmpeg` is installed and used by the app to decode uploaded audio and TTS output.

### Fakes (no network)

- **STT / TTS**: `app.providers.set_stt(provider)` and `app.providers.set_tts(provider)` override the configured
  providers; passing `None` restores the default. Provider shapes:
  - STT: attribute `max_seconds: int` (use 30) and `async def transcribe(self, wav: bytes) -> str`.
  - TTS: `async def synthesize(self, text: str) -> bytes` returning valid WAV bytes (e.g. 200 ms of silence,
    mono, 16-bit, 24000 Hz, built with the `wave` module).
- **LLM**: monkeypatch the module attributes `app.llm.complete_json` and `app.llm.stream_lines`. The app looks them up
  on the `app.llm` module at call time, so patching the module attribute is enough.
  - `async def complete_json(system: str, user: str, schema: type, temperature: float = 0.4, model: str | None = None)`
    returns an instance of `schema` (e.g. `schema(intent=..., question=..., ...)`).
  - `async def stream_lines(system: str, user: str, temperature: float = 0.5, model: str | None = None)` is an async
    generator yielding JSON strings like `'{"say": "...", "draw": []}'`.
  - Fakes can record the arguments they receive to make assertions about prompts.
- Uploaded question audio: any valid WAV longer than 300 ms (e.g. 1 s of silence at 16000 Hz). The STT fake decides
  the transcript.

### Creating lessons in tests

`app.lesson_store.publish_lesson(db, slug: str, title: str, transcript: str, scenes: list[dict]) -> Lesson`

- `db` is a SQLAlchemy session from `app.db.SessionLocal()`; `publish_lesson` adds and flushes but the **caller
  commits**.
- Each scene dict: `{"title": str, "goal": str, "key_terms": list[str], "check_question": dict | None,
  "steps": [{"say": str, "draw": list, "audio_url": str, "duration_ms": int}, ...]}`.
- A `check_question` dict: `{"question", "say", "options": list[str] (2–4), "correct_index": int, "concept",
  "explanation", "audio_url"}`.
- Returns the new `Lesson` row (attributes `id`, `slug`, `title`, `version`).
- Options are shuffled on publish (see Quiz), so tests must read the stored `correct_index` from
  `GET /api/lessons/{slug}` or compare by option text, never assume the original index.

---

## 1. Quiz: shuffled check options (backend)

Module `app.quiz`.

`shuffle_checks(checks: list[dict | None], seed: str) -> list[dict | None]`

- Returns a new list of the same length; `None` entries stay `None` at the same position.
- Does not mutate the input dicts or lists.
- Each returned dict has the same keys and values as the input except `options` (a permutation of the input options)
  and `correct_index` (the index of the originally correct option text in the new order).
- Deterministic: same `checks` and same `seed` give the same result.
- Balanced: among the non-`None` questions that have exactly 4 options, no position `0..3` is the `correct_index`
  more than `ceil(n / 4)` times, where `n` is the number of such questions. (Questions with fewer options just get a
  valid shuffle.)
- `publish_lesson` applies `shuffle_checks` to its scenes' check questions with `seed=slug`.

---

## 2. Lesson versioning (backend)

- `lessons` rows are never deleted by publishing. `publish_lesson` for an existing slug creates a new row with
  `version = previous max version + 1`; the first publish of a slug has `version = 1`.
- Sessions, doubts and check results that belong to older versions remain readable and unchanged.
- `GET /api/lessons` lists one entry per slug: the latest version. Each entry: `{slug, title, scene_count, version}`.
- `GET /api/lessons/{slug}` returns the latest version, including a `version` field (plus existing fields).
- `POST /api/sessions` (`{guest_id, lesson_slug}`) creates the session against the latest version. Response adds
  `lesson_version` to the existing `{id, guest, lesson_slug}`.

---

## 3. Resume position (backend + frontend)

### Backend

`POST /api/sessions/{session_id}/position` with body `{"scene_idx": int, "step_idx": int}`

- `scene_idx` must be a valid scene index of the session's lesson and `step_idx` a valid step index of that scene;
  otherwise **400**. Unknown session: **404**.
- Stores the position on the session (last write wins). Response: **200** `{"scene_idx": int, "step_idx": int}`.

`GET /api/guests/{guest_id}/lessons/{slug}/resume`

- Returns `{"session_id": str, "scene_idx": int, "step_idx": int}` for the guest's most recently updated session
  that (a) is on the **latest** version of the lesson, (b) has a saved position, and (c) is not completed.
  A session is **completed** once `POST /api/sessions/{id}/summary` has succeeded.
- Returns **200 with JSON `null`** when there is no such session. Unknown guest or slug: **404**.

### Frontend

Module `frontend/src/engine/board.ts`:

- `boardBefore(scene: { steps: { draw: DrawAction[] }[] }, stepIdx: number): DrawAction[]` returns the draw actions of
  `steps[0..stepIdx-1]` concatenated in order (array order). `stepIdx <= 0` gives `[]`; `stepIdx` beyond the end gives
  all steps' actions.
- `progressPercent(lesson: { scenes: { steps: unknown[] }[] }, sceneIdx: number, stepIdx: number): number` returns
  `Math.round(100 * done / total)` where `done` is the number of steps in scenes before `sceneIdx` plus `stepIdx`;
  `total` is all steps. Returns `0` when `total` is 0. Result is clamped to `0..100`.

---

## 4. Progress, mastery, history (backend)

### Mastery rule

Module `app.progress`, constants `PRIOR = 0.5`, `ALPHA_CHECK = 0.4`, `ALPHA_DOUBT = 0.15`, `WEAK_BELOW = 0.5`.

- `next_mastery(score: float | None, correct: bool) -> float`: start from `PRIOR` when `score` is `None`;
  return `s + ALPHA_CHECK * ((1.0 if correct else 0.0) - s)`. (First correct → 0.7; first wrong → 0.3.)
- `doubt_mastery(score: float | None) -> float`: start from `PRIOR` when `None`; return `s - ALPHA_DOUBT * s`.
- Results are always within `[0, 1]`.

### Where mastery changes

- `POST /api/sessions/{id}/check` (`{scene_idx, chosen_index}`): updates the guest's mastery of that scene's
  `check_question.concept` with `next_mastery` and increments that concept's `attempts` by 1.
  - Answering the **same scene twice in the same session** returns **409** and changes nothing.
  - Response is unchanged: `{correct, correct_index, explanation, feedback}`.
- A doubt routed with intent `confused`, `repeat` or `prerequisite` applies `doubt_mastery` to the concept of the
  scene it was asked in (`scene_idx` of the doubt), if that scene has a check question. `attempts` is not changed.

### Endpoints

`GET /api/guests/{guest_id}/mastery` → list of `{"concept": str, "score": float, "attempts": int}`, sorted by
`score` ascending, then `concept` ascending. Unknown guest: **404**.

`GET /api/guests/{guest_id}` and other guest responses: `weak_concepts` is the list of concepts with
`score < WEAK_BELOW`, in the same order as the mastery list, followed by any concepts named by session summaries
that are not already in the list.

`GET /api/guests/{guest_id}/progress` → one entry per lesson slug the guest has at least one session in:

```json
{"slug": "...", "title": "...", "version": 2, "percent": 40, "completed": false,
 "checks_correct": 1, "checks_total": 2, "doubts": 3, "sessions": 1,
 "resume": {"session_id": "...", "scene_idx": 1, "step_idx": 2}}
```

- Only sessions on the **latest** version of the slug count toward `percent`, `completed`, `checks_*`, `doubts`,
  `sessions` and `resume`; `title` and `version` are the latest version's.
- `percent`: 100 if any counted session is completed; otherwise the maximum over counted sessions with a saved
  position of `round(100 * steps_before / total_steps)`, where `steps_before` is the number of steps strictly before
  the saved position; 0 if none.
- `resume`: same value as the resume endpoint (or `null`).
- Sorted by the most recent session activity first. Unknown guest: **404**.

`GET /api/guests/{guest_id}/history` → sessions newest first (all versions):

```json
{"session_id": "...", "slug": "...", "title": "...", "version": 1, "created_at": "ISO-8601",
 "completed": true, "checks_correct": 2, "checks_total": 3,
 "doubts": [{"question": "...", "intent": "not_covered", "answered": true}]}
```

- `doubts` are in the order they were asked; `answered` is true once the answer stream has finished for that doubt.
  Unknown guest: **404**.

---

## 5. Smarter routing (backend)

### Intents and delivery

`Intent` becomes: `covered_later`, `not_covered`, `prerequisite`, `confused`, `repeat`, `off_topic`, `unclear`,
`clarify`. `POST /api/sessions/{id}/questions` returns `answer_when`:

| intent | answer_when |
|---|---|
| covered_later | `target_scene` |
| not_covered | `scene_end` |
| prerequisite | `now` |
| confused | `now` |
| repeat | `replay` |
| off_topic, unclear, clarify | `none` |

Response shape (unchanged fields): `{doubt_id, transcript, question, intent, target_scene_idx, answer_when, ack,
callback}`, where `ack` is `{text, audio_url, duration_ms}` and `callback` is either `null` or
`{text, audio_url, duration_ms}` (the callback line is in `callback.text`).

Existing rule kept: `covered_later` is only accepted when `target_scene_idx` is strictly after the current scene,
inside the lesson, and a `callback` is given; otherwise it becomes `not_covered`. `target_scene_idx` and `callback`
are `null` for every intent other than `covered_later`.

Every intent other than `off_topic` and `clarify` uses a fixed, cached ack line (non-empty `ack.text`).

### Classification output

`app.schemas.Classification` gains `confidence: float = 1.0` (0..1). The routing LLM call is
`app.llm.complete_json(system, user, Classification, ..., model=<routing deployment>)`.

### Routing prompt (the `user` argument of the routing call)

- Contains the line `Lesson vocabulary: ` followed by every distinct key term of the lesson's scenes, joined by
  `", "`, in scene order, first occurrence kept.
- Contains, for every scene, its title and goal.
- Contains the narration (`say`) of the **current** scene's steps up to and including the current `step_idx`.
- Does **not** contain the narration of steps in other scenes.
- Contains the `question` text of up to the **5 most recent** earlier doubts **of the same session** (oldest of those
  first), and nothing from other sessions' doubts.
- Contains the student's transcript.

### Routing deployment

`app.config.Settings` gains `azure_openai_routing_deployment: str | None = None` (env
`AZURE_OPENAI_ROUTING_DEPLOYMENT`) and a property `routing_deployment` that returns it, or `azure_openai_deployment`
when unset. The routing call passes `model=settings.routing_deployment`. Answer generation is unchanged
(it may pass `model=None` or omit it).

### Ask back when unsure (`clarify`)

- If the classification has `confidence < 0.6` and intent in {`covered_later`, `not_covered`, `prerequisite`,
  `confused`}, the doubt is stored and returned with intent `clarify`, `answer_when = "none"`, and an `ack` whose
  text contains the rewritten `question` and asks the student to confirm (e.g. "Kya tum yeh pooch rahe ho: …?").
- The next question in the same session, if its transcript is affirmative (see below), skips the LLM call and is
  routed with the **original** classification of the clarify doubt (same intent, question, target, callback; the
  `covered_later` validation still applies). Its response `question` equals the clarify doubt's question.
- After that next question (affirmative or not), the clarify doubt is resolved and never reused. A non-affirmative
  next question is classified normally.

`app.routing.is_affirmative(text: str) -> bool`

- Lowercase, strip punctuation (`.,!?।` and quotes), split on whitespace.
- True when there are 1–4 words and the first word is one of: `haan`, `haa`, `ha`, `han`, `haanji`, `hanji`, `ji`,
  `yes`, `yeah`, `yep`, `sahi`, `bilkul`, `हाँ`, `हां`, `हा`, `जी`. Otherwise false (empty text is false).

### Answers

`GET /api/doubts/{id}/answer` streams NDJSON as today. The prompt passed to `app.llm.stream_lines` includes the
doubt's intent name (e.g. `prerequisite`, `confused`) so the answer can be shaped by intent. After the stream
finishes, the doubt counts as answered.

---

## 6. Bridges (backend)

`GET /api/bridges` returns a list of `{text, audio_url, duration_ms}` read from `$DATA_DIR/media/fillers/bridges.json`;
an empty list when that file does not exist. (Same pattern as `GET /api/fillers`.)

---

## 7. Smooth interruption (frontend, pure modules)

Location of tests: `frontend/tests/*.test.ts`, run with `cd frontend && npm test` (vitest, node environment).
Import app code via the `@/` alias (e.g. `import { resumePoint } from '@/engine/timing'`).

### `@/engine/timing`

- `splitSentences(text: string): string[]` — split after `.`, `?`, `!` or `।` when followed by whitespace; trim each
  part; drop empty parts. Text with no terminator gives `[text.trim()]`; empty or whitespace text gives `[]`.
  Example: `"Dekho yeh wire hai. Current badhao! Kya hoga?"` → `["Dekho yeh wire hai.", "Current badhao!", "Kya hoga?"]`.
  `"Value 2.5 hai. Theek?"` → `["Value 2.5 hai.", "Theek?"]`.
- `sentenceStarts(text: string, durationMs: number): number[]` — start time (ms) of each sentence of
  `splitSentences(text)`, giving each sentence a share of `durationMs` proportional to its character length.
  First value is `0`. Empty text gives `[]`.
  Example: sentences of lengths 10 and 30 with `durationMs = 4000` → `[0, 1000]`.
- `resumePoint(text: string, durationMs: number, pausedAtMs: number): number` — where to restart after an
  interruption. Constants exported: `MAX_REWIND_MS = 6000`, `FALLBACK_REWIND_MS = 2000`.
  - `pausedAtMs <= 0` → `0`.
  - Let `start` be the start of the last sentence whose start is `<= pausedAtMs` (using `sentenceStarts`; if the text
    has no sentences, treat `start` as `0`).
  - If `pausedAtMs - start <= MAX_REWIND_MS` → return `start`.
  - Otherwise → return `max(0, pausedAtMs - FALLBACK_REWIND_MS)`.
  - The result is never greater than `pausedAtMs` and never negative.

### `@/engine/fade`

- `fadeTo(el: { volume: number }, target: number, ms: number): Promise<void>` — changes `el.volume` linearly to
  `target` (clamped to `[0, 1]`) over `ms`, stepping with `setTimeout` (not `requestAnimationFrame`).
  - Resolves when done, with `el.volume === target` exactly.
  - `ms <= 0` sets the volume immediately and resolves.
  - While fading, volume is always between the start value and the target.
  - A newer `fadeTo` on the same element cancels the older one: the older promise resolves and stops changing the
    volume; only the newer fade continues.
  - Exported constant `FADE_MS = 150` (default duration the engine uses).

### `@/engine/vad`

- `rms(samples: ArrayLike<number>): number` — root mean square; `0` for an empty input.
- `class SilenceDetector` with constructor options (all optional) `{ threshold = 0.02, silenceMs = 1200,
  noSpeechMs = 6000, maxMs = 25000 }` and method `push(level: number, atMs: number): VadState` where
  `VadState = 'waiting' | 'speaking' | 'end' | 'no_speech' | 'max'`.
  - The first `push` sets the start time `t0 = atMs`.
  - Check order on each push:
    1. If a terminal state (`end`, `no_speech`, `max`) was already returned, return it again.
    2. If `atMs - t0 >= maxMs` → `max`.
    3. If `level >= threshold` → mark speech heard, remember `atMs` as last voice, return `speaking`.
    4. If speech was never heard: `atMs - t0 >= noSpeechMs` → `no_speech`, else `waiting`.
    5. Speech was heard and `level < threshold`: `atMs - lastVoice >= silenceMs` → `end`, else `speaking`.
- `pressKind(downAtMs: number, upAtMs: number, tapMaxMs = 300): 'tap' | 'hold'` — `tap` when
  `upAtMs - downAtMs < tapMaxMs`, else `hold`.

---

## Behaviour implemented without automated tests (verified by build and manual run)

These live in the `Tutor` engine and `MicButton`, which depend on real audio elements and the microphone:

- On mic press: fade the lesson audio out over `FADE_MS`, then pause. On resume: seek to `resumePoint(...)` of the
  current clip and fade back in. The same applies to the pause button.
- `answer_when = "now"`: after the ack, play the answer on the doubt board immediately, then restore the lesson
  board, play a bridge line, and resume the clip from `resumePoint`.
- `answer_when = "replay"`: after the ack, restart the current step from 0.
- After any delivered answer, play a random bridge line before going back to the lesson.
- Doubts routed while an answer is playing are delivered in the same delivery block, right after it.
- Mic: a short tap starts tap-to-talk, which stops on its own using `SilenceDetector` (`end` sends, `no_speech`
  cancels, `max` sends); a long press keeps hold-to-talk (send on release). Space key behaves the same.
- Position is saved (`POST /position`) when each step starts. The lesson page offers "Continue from scene N" when
  the resume endpoint returns a position; the engine starts at that step with `boardBefore(...)` already drawn.
- Home page shows each lesson's `percent` from the progress endpoint; the lesson progress bar uses
  `progressPercent`.

---

## 8. Resume with previous doubts (added 2026-10-22)

| Change | Improvement for the student / team |
|---|---|
| Session state endpoint | Resuming a lesson brings back the student's earlier doubts, promised callbacks and answered checks. |
| `restoreSession` | Unanswered doubts are answered again at the end of the scene; answered checks aren't asked twice (which would get a 409). |

### Backend: `GET /api/sessions/{session_id}/state`

Unknown session: **404**. Response:

```json
{"session_id": "...", "completed": false,
 "position": {"scene_idx": 1, "step_idx": 2},
 "answered_checks": [0],
 "doubts": [{"id": "...", "scene_idx": 0, "step_idx": 1, "question": "...", "intent": "covered_later",
             "target_scene_idx": 2, "answered": false,
             "callback": {"text": "Tumne pucha tha ...", "audio_url": "/media/doubts/<id>_callback.wav", "duration_ms": 0}}]}
```

- `position`: the saved position, or `null` when none was saved.
- `completed`: true once the summary was generated.
- `answered_checks`: scene indexes with a recorded check answer in this session, ascending, no duplicates.
- `doubts`: every doubt of this session in the order asked (including `clarify`, `off_topic` and `unclear` ones), with
  the `question` and `intent` as returned by `POST /questions` (for a confirmed clarify: the restored intent).
- `answered`: true once the answer stream has finished for that doubt.
- `callback`: for a `covered_later` doubt, `{text, audio_url, duration_ms}` where `text` is the callback line from
  routing and `audio_url` equals the `callback.audio_url` returned by `POST /questions`; `null` for every other intent.

### Frontend: `@/engine/restore`

`restoreSession(state: SessionState, resumeSceneIdx: number): Restored` where `SessionState` is the JSON above and

```ts
interface Restored {
  doubts: { id: string; question: string; intent: string; targetSceneIdx: number | null;
            status: 'promised' | 'queued' | 'answered' | 'dismissed' }[]   // same order as state.doubts
  callbacks: { sceneIdx: number; doubtId: string; spoken: { text: string; audio_url: string; duration_ms: number } }[]
  redeliver: string[]        // doubt ids to answer again at the end of the resumed scene, in asked order
  answeredChecks: number[]   // copy of state.answered_checks
}
```

Rules for each doubt, first match wins:
1. `answered` is true → status `answered`.
2. intent `covered_later`, `callback` not null and `target_scene_idx > resumeSceneIdx` → status `promised`, and a
   `callbacks` entry `{sceneIdx: target_scene_idx, doubtId: id, spoken: callback}` (in asked order).
3. intent `covered_later` otherwise → status `answered` (its scene has already been reached).
4. intent `not_covered`, `prerequisite` or `confused` → status `queued`, and its id goes to `redeliver`.
5. intent `repeat` → status `answered`.
6. any other intent (`off_topic`, `unclear`, `clarify`) → status `dismissed`.

`targetSceneIdx` is `target_scene_idx`. Does not mutate `state`.

---

## 9. Remediation after a wrong check answer (added 2026-10-22)

| Change | Improvement for the student / team |
|---|---|
| Option-specific hint, then one retry | The student hears why *their* pick is wrong (the likely misconception) and gets to fix it themselves, instead of just being told the right letter. |
| Re-teach on the doubt board after a second miss | The idea is explained again in a different way with a drawing, before moving on. |
| "Kahan confusion hua?" follow-up by voice | The student can say what confused them; the answer is generated right away and knows which options they picked. |
| Twin question later | A different question on the same concept after the next scene checks whether the re-teach worked; mastery recovers only then. |

All remediation content is written and narrated **offline** (new prep stage `remedy`), so it plays with no live
LLM or TTS wait. Lessons published without it keep the old behaviour (wrong → reveal the answer).

### Published check question (extra optional keys)

A scene's `check_question` dict may carry, besides the keys of the Test setup section:

- `why_wrong`: list parallel to `options`. For a wrong option, `{text, audio_url, duration_ms}`: a spoken hint naming
  the likely misconception behind that pick, without revealing the right option. `null` for the correct option (and
  for any option without a hint).
- `reteach`: list of 2–4 clips `{say, draw, audio_url, duration_ms}`, a fresh explanation for the doubt board.
- `followup`: `{text, audio_url, duration_ms}`, the fixed line `CHECK_FOLLOWUP` asking where the confusion was.
- `twin`: `{question, say, options, correct_index, explanation, audio_url}`, a different question on the same concept.

`app.quiz.shuffle_checks`: when a question has a `why_wrong` list of the same length as `options`, it is permuted
together with `options` (each hint stays with its option text). All other rules of section 1 still hold.
`publish_lesson` additionally shuffles the `twin` questions with `shuffle_checks(twins, seed=f"{slug}:twin")`
(scenes without a twin pass `None`), and stores the result as each check question's `twin`.

### Fixed lines (`app.prompts`)

`CHECK_LINES` is a dict of fixed spoken feedback (cached audio):
- `"correct"`: `"Bilkul sahi! Shabaash."`
- `"wrong_try_again"`: first wrong answer to a question that has `why_wrong` (does not name the right option).
- `"retry_correct"`, `"twin_correct"`: praise after a correct retry / twin answer.

`CHECK_FOLLOWUP` is the line stored as `followup`.

Wrong feedback that reveals the answer ends with `"Sahi jawab option {letter} hai."`, where `letter` is `A`–`D` for
the judged question's `correct_index`.

### `POST /api/sessions/{id}/check` (first answer)

Unchanged contract (response keys, 409 on a repeat, mastery), except the feedback text on a **wrong** answer:
- question has a non-empty `why_wrong` → `CHECK_LINES["wrong_try_again"]` (no answer revealed);
- otherwise → reveals the answer as before.
A correct answer's feedback text is `CHECK_LINES["correct"]`.

### `POST /api/sessions/{id}/check/retry`

Body `{"scene_idx": int, "chosen_index": int}`. Response: same shape as `/check`
(`{correct, correct_index, explanation, feedback}`), judged against the same question.
- Unknown session **404**; scene without a check question **404**.
- **409** (and nothing changes) when this session has no first answer for that scene, the first answer was correct,
  or a retry for that scene was already recorded.
- Mastery of the concept: `retry_mastery(score, correct)`; `attempts` is **not** changed.
- Feedback text: correct → `CHECK_LINES["retry_correct"]`; wrong → reveals the answer.
- A retry is not a check result: `answered_checks`, `checks_correct` and `checks_total` (progress, history) only count
  first answers.

### `POST /api/sessions/{id}/check/twin`

Body `{"scene_idx": int, "chosen_index": int}`, where `chosen_index` indexes the **twin's** options. Response: same
shape as `/check`, with the twin's `correct_index` and `explanation`.
- Unknown session **404**; scene without a check question or without a `twin` **404**.
- **409** (nothing changes) when there is no first answer for that scene in this session, the first answer was
  correct, or the twin was already answered.
- Mastery: `next_mastery(score, correct)` and `attempts` + 1 (it is a new question).
- Feedback: correct → `CHECK_LINES["twin_correct"]`; wrong → reveals the twin's answer.
- Not counted in `answered_checks` / `checks_*`.

### Mastery rule (`app.progress`)

`ALPHA_RETRY = 0.2`. `retry_mastery(score, correct)`: start from `PRIOR` when `None`; return
`s + ALPHA_RETRY * ((1.0 if correct else 0.0) - s)`, within `[0, 1]`. (After a first wrong answer at 0.3: correct
retry → 0.44, wrong retry → 0.24.)

### Summary

The summary LLM call's `user` JSON lists, per first answer in `check_results`, `retry_correct` and `twin_correct`
(`true`/`false`, or `null` when not attempted).

### Doubts during a check

`POST /api/sessions/{id}/questions` accepts an optional form field `during_check` (`"true"`/`"false"`, default false).
When true:
- `off_topic`, `unclear` and `continue` stay as they are; every other intent becomes `confused` (`answer_when = "now"`,
  `target_scene_idx` and `callback` null);
- (changed in section 11) a forced `confused` with confidence < 0.6 becomes `clarify` as usual, and a following "haan"
  is answered now.

`GET /api/doubts/{id}/answer`: when this session has a first check answer for the doubt's `scene_idx` recorded
**before** the doubt was asked, the prompt passed to `stream_lines` contains the line `Student's check answers:` and,
after it, the check `question`, the text of every option the student picked (first answer and retry, if any), the
correct option's text and the `explanation`. Otherwise the prompt contains no `Student's check answers:` line.

### Prep stage `remedy`

`STAGES` becomes `transcript, outline, scenes, audio, remedy`. For each scene the LLM (`REMEDY_SYSTEM`) returns
`app.schemas.RemedyDraft`: `why_wrong: list[OptionHint]` (`{option: int, hint: str}`, one per wrong option, labelled
with its index), `reteach: list[StepDraft]`
(2–4), `twin: TwinQuestion` (`question, say, options (2–4), correct_index, explanation`). Like `SceneDraft`, it rejects
Devanagari in any spoken text (`why_wrong`, reteach `say`, twin `say`), which makes the LLM retry. The stage then
synthesizes the hints, re-teach steps, twin question and follow-up line, caches the result in `remedy.json` and
`load_db` merges it into each scene's `check_question`.

### Frontend: `@/engine/remedy`

- `wrongNext(q: CheckQuestion, attempt: 'first' | 'retry'): 'retry' | 'reteach' | 'reveal'`
  - `'first'` and `q.why_wrong` has at least one non-null entry → `'retry'`;
  - otherwise `q.reteach` is non-empty → `'reteach'`;
  - otherwise `'reveal'`.
- `twinSlot(sceneCount: number, sceneIdx: number): number | 'end'`: the scene after whose end (answers delivered,
  before its own check) the twin of `sceneIdx` is asked: `sceneIdx + 1` when that scene exists, else `'end'`
  (after the last scene, before the summary).

### Behaviour without automated tests (engine + `CheckCard`)

- Wrong first answer with hints: play the feedback, then the chosen option's hint; the wrong option is marked and
  disabled, the right one is **not** shown, and the student picks again (`/check/retry`).
- Wrong again (or no hints): play the re-teach clips on the doubt board, then show the answer and explanation, play
  `followup`, and open the mic. A doubt asked now is sent with `during_check=true` and its answer plays at once on the
  doubt board; "Aage chalein" continues the lesson.
- If the first answer was wrong and the question has a twin, the twin is asked at `twinSlot(...)`. A twin answer
  just shows the result and explanation. Twins are not restored when a session is resumed.

---

## 10. Per-lesson tutor voice, including a cloned voice (added 2026-10-22)

| Change | Improvement for the student / team |
|---|---|
| Lesson voice (stock or cloned) | A lesson can be narrated in the real teacher's voice (cloned with permission), while other lessons keep the stock voice. |
| Voice gender | A male voice gets masculine first-person verbs ("samjhata hoon") in narration, acks, answers and UI. |

- `app.voices`: `get_voice(name)` returns a `Voice {name, gender, stock, embedding}`; `None`/`"default"` is the configured
  stock voice (`GNANI_TTS_VOICE`, gender `TUTOR_GENDER`, default `female`). Other voices are `data/voices/<name>.json`;
  names must match `^[a-z0-9][a-z0-9-]{0,59}$` (`voice_path` raises `ValueError` otherwise).
  `python -m app.voices clone <audio> --name <name> --gender male|female --start S --seconds 5..30` cuts the reference
  clip, calls Gnani `POST /api/v1/tts/voice-clone/embeddings` and saves the embedding.
- `audio.speak(text, path, voice=None)` synthesizes with `current_voice` set to that voice; `speak_cached(text, voice=None)`
  caches per voice (`cache/<voice>/…`; the default voice keeps `cache/…`). Gnani sends a cloned voice as
  `speaker_embedding` with model `vachana-vc-v1`.
- `lessons.voice` (nullable column, added by `db.migrate()`); `publish_lesson(..., voice=None)`. `GET /api/lessons/{slug}`
  adds `voice` and `voice_gender`.
- Everything spoken for a session uses its lesson's voice: acks, clarify/off-topic lines, callbacks, answers, check feedback,
  summary. Routing, answer and summary system prompts use `for_gender(system, gender)`, which swaps `FEMALE_RULE` for
  `MALE_RULE`; fixed lines go through `line_for(text, gender)`.
- `GET /api/fillers?voice=` and `GET /api/bridges?voice=` read `media/fillers/<voice>/…json` (default voice: `media/fillers/`);
  an unknown or invalid voice gives `[]`.
- Prep `--voice NAME` narrates every stage in that voice and writes the scene and remedy prompts for its gender (changing the
  voice of an existing lesson needs `--redo scenes`).

---

## 11. Content quality fixes from the nomenclature-01 review (added 2026-10-22)

| Change | Problem it fixes (seen in a real session) |
|---|---|
| `review` prep stage + remedy review | A check question marked a wrong chemistry answer correct ("OH outranks CHO"); a twin had two correct options. |
| Grounded answers (`source` line) | A doubt answer contradicted the teacher's DU formula. |
| Earlier doubts in the answer prompt | A repeated "kya yeh sahi hai?" got a second answer that again skipped the yes/no. |
| `continue` intent | "Sab clear hai, aage chalo" was routed `off_topic`. |
| Check card in routing; ask back during checks | "Confusion point two mein hua" was answered by guessing what "point two" meant. |
| Hint leak check | A hint said the right answer, so the retry measured nothing. |
| Recorded skips | A twin left no trace; skipped and never-shown could not be told apart. |
| Prep model setting | gpt-4o does not reason before answering; a reasoning model is better for offline content. |

### Prep model
`Settings.azure_openai_prep_deployment` (env `AZURE_OPENAI_PREP_DEPLOYMENT`, property `prep_deployment`, falls back to
`azure_openai_deployment`) and `azure_openai_prep_reasoning_effort`. `app.llm.prep_json(system, user, schema)` calls
`complete_json(..., model=prep_deployment)`, adding `reasoning_effort=<effort>` (and no temperature) when the effort is
set. Outline, scenes, review and remedy use it. `complete_json` gains `reasoning_effort: str | None = None`.

### Prep stage `review`
`STAGES = transcript, outline, scenes, review, audio, remedy`. Per scene, `review_scene(meta, scene, transcript, voice)`
calls `prep_json(REVIEW_SYSTEM, user, CheckReview)`; `user` contains the lecture transcript, each step's narration and
the check question with `Option i: ...` lines. `CheckReview = {issues, narration_issues, check_question}` (Devanagari
in `say` is rejected). The returned dict is cached in `review.json`; audio and remedy use its `check_question`.
Narration issues are only logged at the end of the run. A lesson that has `audio.json` but no `review.json` redoes
audio and remedy after the review.

### Remedy review and leak check
`make_remedy(meta, scene, voice=None, transcript="")` sends the transcript to `REMEDY_SYSTEM`, then reviews the draft
with `prep_json(REMEDY_REVIEW_SYSTEM, ..., RemedyReview)` (`{issues, remedy: RemedyDraft}`) and uses `remedy`. Hints
for which `app.schemas.leaks_answer(hint, correct_option)` is true are dropped: after lowercasing and replacing
punctuation with spaces, the correct option's text occurs in the hint as whole words; options of one word under 6
characters are never judged. Reviewed parts whose spoken text mentions "lecture", "transcript" or "review" (whole word, any case) are
replaced by the un-reviewed draft's part (per hint, the whole re-teach, the whole twin).

### Doubts
- Intent `continue` (→ `answer_when = "none"`, fixed ack `FIXED_ACKS["continue"]`). On the check card the player treats it
  like "Aage chalein".
- With `during_check`, the routing prompt contains `Check question on screen: <question>`, one line `<letter>. <option>`
  per option (A–D), and `Student picked (wrong): <letters>`. Without it, no such section.
- The answer prompt contains `Earlier doubts in this session` with up to 3 earlier doubts of the session (not
  `clarify`), each with the tutor's spoken answer; absent for the first doubt.
- `ANSWER_SYSTEM` asks for a first line `{"source": ...}` (what the lecture says); it fails the step schema, so it is
  neither spoken nor stored.

### `POST /api/sessions/{id}/check/skip`
Body `{"scene_idx": int, "twin": bool = false}` → `{"ok": true}`. 404 for an unknown session, a scene without a check
question, or `twin` without a twin. Records once (repeat calls are fine), changes no mastery and no check counts. The
summary `user` JSON gets `twin_skipped` per check result and `skipped_checks` (scene indexes). The player calls it when
a card is closed before its answer was shown.

