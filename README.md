# Voice AI Tutor (demo)

This demo turns a recorded lecture into a **narrated whiteboard lesson** in Hinglish. As the tutor speaks,
the board is drawn in sync with the voice. A student can **interrupt at any time by voice** to ask a doubt.

The tutor doesn't always answer straight away. Like a real teacher, it decides when to answer:

| Student asks… | Tutor says | What happens |
|---|---|---|
| Something a later scene covers | "Bahut accha sawal! Yeh hum thodi der mein dekhenge…" | The question is saved. When that scene starts: "Tumne pucha tha ki…" |
| A real doubt that no later scene covers | "Accha sawal hai! Yeh point khatam karke main iska jawab deti hoon." | The answer is prepared in the background and played at the end of the scene, on a separate "Doubt" board |
| "Ek baar phir se samjhaiye" | "Bilkul, isko ek naye example se dobara samajhte hain." | The current point is explained again with a new example, right after the current step |
| Something off-topic | A short, friendly reply | The lesson continues |

Each scene ends with a multiple-choice check question. The session ends with a spoken summary and a list of
concepts to revise.

---

## How it works

There are two halves. Most of the work happens **once, offline**. Only question handling runs live.

### 1. Offline prep (once per lecture)

```
lecture.mp3
  └─ ffmpeg: cut into ~28 s chunks
      └─ Gnani STT ............................ transcript (Hindi)
          └─ Azure OpenAI gpt-4o: outline ..... 4–6 non-overlapping scenes
              └─ gpt-4o: each scene ........... steps of { say, draw[] } + check question
                  └─ Gnani TTS ................ one audio clip per step (and its duration)
                      └─ Postgres + data/media/
```

- `say` is what the tutor speaks: Hinglish in Roman script, written so it can be read aloud
  ("mu naught by four pi", not "μ₀/4π").
- `draw` is a list of whiteboard actions: text, line, arrow, rect, circle, angle, ⊗/⊙ field marks, and highlight.
  The LLM writes these actions; the browser animates them as pen strokes. No images are generated.
- Every stage is cached in `data/lessons/<slug>/`, so a rerun only redoes what changed.

### 2. Live session (in the browser)

```
Player plays step audio ──► board animates the step's draw actions over the clip's duration
      │
Student holds mic ──► lesson pauses ──► filler line plays ("Accha sawal!")
      │
      ▼
Backend: STT ──► gpt-4o classifies the doubt, seeing every scene so it can look ahead
      │
      ▼
Spoken acknowledgement ──► lesson resumes
      │
      ├─ covered later  → callback line plays when the target scene starts
      ├─ not covered    → answer streamed now, played at the end of the scene
      └─ repeat         → answer streamed now, played at the end of the step
```

- **Code, not the LLM, owns the lesson position.** The browser engine (`frontend/src/engine/tutor.ts`) walks
  scene → step and decides when to pause, answer and resume.
- **Answers stream sentence by sentence.** The LLM writes one `{say, draw}` per line. Each line goes to TTS as
  soon as it is complete, so the first sentence can play while the rest is still being written.
- **Hiding the delay:** routing a doubt takes about 2–4 s, so a pre-recorded filler line plays meanwhile. Answers
  are prepared while the lesson carries on.

---

## Tech stack

| Part | Tool |
|---|---|
| Frontend | React + TypeScript (Vite), shadcn/ui, Tailwind CSS v4, Zustand, axios |
| Backend | FastAPI, Pydantic, SQLAlchemy |
| Database | Postgres 16 (Docker) |
| LLM | Azure OpenAI `gpt-4o` |
| Speech to text / text to speech | Gnani (Prisma STT, Timbre TTS); swappable, see [Speech providers](#speech-providers) |
| Audio processing | ffmpeg |

Users are guests: no login, just a name and class, kept in the browser.

---

## Setup

### Requirements

- Docker
- Python 3.11+
- Node.js 20+
- ffmpeg (`brew install ffmpeg`)
- A Gnani API key and an Azure OpenAI key with a `gpt-4o` deployment

### 1. Configure

```bash
cp .env.example .env     # then fill in the keys
```

| Key | Meaning |
|---|---|
| `GNANI_API_KEY` | Gnani API key |
| `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT` | Azure OpenAI resource |
| `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION` | e.g. `gpt-4o`, `2024-10-21` |
| `STT_PROVIDER`, `TTS_PROVIDER` | Which speech vendor to use (`gnani`) |
| `GNANI_TTS_VOICE`, `GNANI_TTS_MODEL`, `GNANI_TTS_LANGUAGE` | Tutor voice (default `Shivani`, `timbre-v3.0`, `hi-en`) |
| `DATABASE_URL` | Defaults to the Docker Postgres below |

### 2. Install

```bash
docker compose up -d                                   # Postgres on localhost:5440

cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cd ../frontend
npm install
```

### 3. Create a lesson from a lecture

Put the lecture audio (mp3, wav, …) in `input_audio/`, then:

```bash
cd backend
.venv/bin/python -m app.prep "../input_audio/<lecture>.mp3" --slug magnetism-01 --start-min 0 --minutes 12
```

| Option | Meaning |
|---|---|
| `--slug` | Lesson id (rerunning with the same slug replaces the lesson) |
| `--start-min`, `--minutes` | Which part of the recording to use (10–15 min makes a ~4 min lesson) |
| `--redo STAGE` | Regenerate from a stage onward: `transcript`, `outline`, `scenes` or `audio` |

Example: keep the transcript but rewrite the scenes and their audio:

```bash
.venv/bin/python -m app.prep "../input_audio/<lecture>.mp3" --slug magnetism-01 --redo scenes
```

A 12-minute slice takes about 3–4 minutes to prepare. Most of that is TTS, because Gnani allows one request at a time.

### 4. Run

Run each in its own terminal:

```bash
cd backend  && .venv/bin/uvicorn app.main:app --port 8010 --reload
cd frontend && npm run dev
```

Open **http://localhost:5173**.

---

## Using the app

1. **Enter your name, class and language.** No sign-up. You stay remembered in this browser.
   Use "Not <name>?" to switch student.
2. **Pick a lesson** and press **Start lesson**.
3. **Watch and listen.** The board fills in as the tutor explains. The caption below the board shows what she's saying.
4. **Ask a doubt.** **Hold** the mic button (or hold **Space**), speak in Hindi, Hinglish or English, then
   **release**. Allow microphone access the first time. A recording can be up to 25 seconds.
   - The **Your doubts** panel shows each doubt and when it will be answered:
     *Aage aayega* (in a later scene), *Scene ke baad* (end of this scene), *Abhi* (now), *Done*.
   - Answers appear on a yellow board marked **Doubt**. Then the lesson board comes back.
5. **Pause / resume** with the button next to the mic.
6. **Check question:** after each scene, pick an option, hear the feedback, then press **Aage chalein**
   (or **Skip**).
7. **Summary:** at the end, the tutor gives a spoken recap, things to revise, and your weak concepts
   (saved for next time).

---

## Project layout

```
backend/app/
  prep/__main__.py     offline pipeline (transcript → outline → scenes → audio → DB)
  api/questions.py     live doubts: STT, routing, streamed answers
  api/sessions.py      guests, sessions, check answers, summary
  api/lessons.py       lessons and filler audio
  prompts.py           every LLM prompt, speech rules and the whiteboard DSL
  schemas.py           draw actions and API/LLM models
  providers/           STT/TTS vendors (Gnani)
  audio.py             ffmpeg helpers, TTS → stored audio
frontend/src/
  engine/tutor.ts      playback engine: lesson pointer, pausing, doubt delivery
  components/Board.tsx SVG whiteboard and stroke animation
  store/               Zustand stores (player state, guest)
data/                  generated: lesson caches, audio (data/media), prep logs
docs/plan/             design decisions
```

## Speech providers

STT and TTS sit behind two small interfaces in `backend/app/providers/base.py`. To add Sarvam, ElevenLabs,
OpenAI, …:

1. Create `backend/app/providers/<vendor>.py` with a class that has `async transcribe(wav) -> str` (and
   `max_seconds`), or `async synthesize(text) -> bytes`.
2. Register it in `backend/app/providers/__init__.py`.
3. Set `STT_PROVIDER=<vendor>` / `TTS_PROVIDER=<vendor>` in `.env`. Then rerun prep with `--redo audio`, so the
   narration uses the new voice.

## Troubleshooting

| Problem | Fix |
|---|---|
| No lessons on the home page | Run the prep command (step 3) and check the backend is running on :8010 |
| Mic button does nothing | Allow microphone access for localhost in the browser's site settings |
| `429 Only 1 active request` from Gnani | Another process is calling Gnani TTS at the same time; wait or rerun |
| Port 8010 / 5440 / 5173 already in use | Change it in `docker-compose.yml`, `vite.config.ts` (proxy target) and `.env` |
| Narration sounds wrong | Try another voice: `GNANI_TTS_VOICE` (e.g. `Shivani`, `Avinash` for `timbre-v3.0`), then `--redo audio` |

## Known limits (demo)

- An answer to a doubt can draw only simple diagrams. Complex figures need templates.
- Routing a doubt takes 2–4 s (covered by the filler line). Instant answers are out of scope.
- Answers stick to the lecture transcript. Anything beyond it gets "yeh hum aage padhenge".
- There are no automated tests yet.
