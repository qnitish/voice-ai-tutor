# Improvements spec: smooth interruption, guest progress, smarter routing, speed + quiz

Status: agreed scope (2026-10-21). This file is the **contract for the tests**. Tests are written from this file
only, without reading the implementation. The implementation must make them pass without editing them.

Out of scope for this round: content evaluation (`review` stage), math checks, review-at-session-start, re-teaching
after a wrong check answer, streaming upload, a second TTS provider.

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
