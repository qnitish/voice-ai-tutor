# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A demo voice tutor for Class 6–12 students (Hinglish). A recorded lecture is turned offline into a narrated
whiteboard lesson; the student can interrupt by push-to-talk and the tutor routes the doubt (answer later in
the lesson, at the end of the scene, right now, or redirect). Design decisions and their reasons are in
`docs/plan/voice-tutor-decisions.md`; read it before changing the flow.

## Commands

```bash
docker compose up -d                      # Postgres 16 on localhost:5440 (db/user/pass: voice_tutor/tutor/tutor)

# Backend (Python 3.11, venv in backend/.venv)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8010 --reload

# Offline lesson prep: audio -> transcript -> scenes -> narration audio -> Postgres
.venv/bin/python -m app.prep "../input_audio/<file>.mp3" --slug magnetism-01 --start-min 0 --minutes 12
.venv/bin/python -m app.prep "../input_audio/<file>.mp3" --slug magnetism-01 --redo scenes   # rerun a stage and all later ones

# Frontend (Vite dev server on :5173, proxies /api and /media to :8010)
cd frontend
npm install && npm run dev
npm run build        # tsc -b + vite build
npm run lint         # oxlint

# Tests (written test-first from docs/plan/improvements-spec.md; don't edit them to make code pass)
cd backend && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/pytest   # uses DB voice_tutor_test, fakes LLM/STT/TTS
cd backend && .venv/bin/pytest tests/test_routing.py -k clarify                 # single file / test
cd frontend && npm test                                                          # vitest, pure engine modules
```

Test checksums are in `docs/plan/tests.lock`. Ports 8000 and 5432 are used by other local projects, hence 8010 and 5440.
Secrets live in the root `.env` (read by `backend/app/config.py`); `.env.example` lists every key.

## Architecture

**Offline prep** (`backend/app/prep/__main__.py`) runs in stages, each cached as JSON in `data/lessons/<slug>/`:
1. `transcript`: ffmpeg cuts the audio slice into STT-sized chunks (provider `max_seconds`), transcribed in parallel.
2. `outline`: LLM plans 4–6 non-overlapping scenes.
3. `scenes`: LLM writes each scene **sequentially** (later scenes see earlier narration to avoid repeats) as steps
   `{say, draw[]}` plus an MCQ check question. `SceneDraft` rejects Devanagari in `say`, which makes the LLM retry.
4. `audio`: TTS per step and check question → `data/media/lessons/<slug>/`.
Then fillers, bridges and fixed acks are pre-synthesized and the lesson is published with `lesson_store.publish_lesson`
as a **new version** (old versions and their sessions are kept; the API serves the latest). Check-question options are
shuffled on publish (`quiz.shuffle_checks`) so the answer isn't always A.

**Live API** (`backend/app/api/`):
- `questions.py`: `POST /api/sessions/{id}/questions` runs STT, then an LLM classification (deployment
  `settings.routing_deployment`) given a compact prompt: lesson vocabulary, every scene's title/goal/terms, the current
  scene's narration so far and the session's last 5 doubts. Intents map to `answer_when`: `covered_later→target_scene`,
  `not_covered→scene_end`, `prerequisite/confused→now`, `repeat→replay`, `off_topic/unclear/clarify→none`. The server
  validates that `target_scene_idx` is really ahead, and downgrades to `not_covered` if it isn't. Confidence < 0.6 turns
  the doubt into `clarify` ("Kya tum yeh pooch rahe ho…?"); a following "haan" (`routing.is_affirmative`) replays the
  stored classification without an LLM call. Acks are fixed lines (`FIXED_ACKS`, cached audio) except off-topic and
  clarify. The "Tumne pucha tha…" callback is synthesized in a background task. LLM calls go through `llm.complete_json`
  / `llm.stream_lines` looked up on the module (tests monkeypatch them); providers can be swapped with `set_stt/set_tts`.
- `GET /api/doubts/{id}/answer` streams NDJSON: the LLM writes one `{say, draw}` object per line, and each line's TTS
  starts as soon as the line is complete (producer/consumer queue), so the first line plays while later lines generate.
  The finished answer is stored on the `Doubt` and replayed from there.
- `sessions.py`: guests (no auth), sessions, `position` (resume point), `check` (one answer per scene per session; updates
  concept mastery, see `progress.py`), `summary` (marks the session completed), `state` (position, answered checks and
  doubts, used to resume with earlier doubts via `engine/restore.ts`), and per-guest `resume`, `progress`,
  `mastery`, `history`. `weak_concepts` = concepts with mastery < 0.5 plus those named by summaries.

**Providers** (`backend/app/providers/`): STT/TTS are `Protocol`s, selected by `STT_PROVIDER`/`TTS_PROVIDER`. To add a vendor,
write a module and register it in `providers/__init__.py`. Gnani specifics: auth header `X-API-Key-ID`; STT accepts at most
~30–60 s per request; the account allows **one TTS request at a time** (`GnaniTTS` serializes with a semaphore); its WAV
headers carry placeholder sizes, so `audio.to_wav` always re-encodes TTS output and measures duration from the PCM.

**Prompts** all live in `backend/app/prompts.py`: shared `SPEECH_RULES` (Roman-script Hinglish, speakable maths, female
first-person verbs to match the voice) and `DRAW_SPEC` (the whiteboard DSL). The draw action schema is in
`backend/app/schemas.py`, mirrored in `frontend/src/lib/types.ts` and rendered in `frontend/src/components/Board.tsx`.
Change all three together. `clean_draw` drops invalid actions instead of failing the step.

**Frontend playback** (`frontend/src/engine/tutor.ts`): the `Tutor` class owns the lesson pointer (scene → step loop) and
writes state into the Zustand store `store/player.ts`; components only read the store and call engine methods.
- Draw actions of a clip are spread over its audio duration (`schedule`); `Board` renders progress from
  `audio.currentTime`, so pausing the audio pauses the drawing.
- `gate()` blocks the loop while the user has paused or the mic is held. Fillers, acks, bridges, feedback and "now"
  answers play on a second `<audio>` element while the main one is held. Holding fades out (`fade.ts`); resuming seeks
  back to the start of the interrupted sentence (`timing.resumePoint`) and fades in.
- `MicButton`: a quick tap is tap-to-talk, ended by `vad.SilenceDetector`; a long press is push-to-talk.
- Answers are prefetched as soon as a doubt is routed and delivered at the matching boundary (`deliver`, which also picks
  up doubts asked during it) or immediately (`interject` for "now") on a separate "doubt" board layer; then a bridge
  line plays and the lesson board is restored. Promised doubts play their callback when the target scene starts.
- The position is saved at each step; `start(lesson, session, from)` resumes there with `board.boardBefore` drawn.
- A new `Tutor` is created per `LessonPage` mount (StrictMode mounts twice; `stop()` is permanent).

UI uses shadcn/ui (radix-nova preset; add components with `npx shadcn@latest add <name>`), Tailwind v4, axios for
JSON calls, and `fetch` only for the NDJSON answer stream.
