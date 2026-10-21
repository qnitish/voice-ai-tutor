# Voice Tutor Demo: Decisions & Inputs

As of 19 Oct 2026. Summary of the planning chat for the voice-agent tutor demo.

## 1. Inputs (what we have and want)

- **Audience:** students of Class 6–12.
- **Source content:** lecture videos on YouTube, from which we get the transcript.
- **Goal:** a quick demo, not a polished product. It should show the flow and its complexity.
- **Experience wanted:**
  - From the transcript, generate a lecture split into scenes.
  - Each scene draws on a board in "real time" while tutor audio explains the point.
  - The student can interrupt at any point and ask a question about the topic.
  - The tutor doesn't have to answer immediately. It can buy time ("we'll learn this in the next 5 min, let's finish this first").
- **Budget:** free or near-free APIs. Sarvam was the main option, with Maya (Maya Research) and ElevenLabs considered.
- **Personalisation:** needs student data plus tools the LLM can call.

## 2. Key decisions

| # | Decision | Why |
|---|---|---|
| 1 | Pre-generate the lecture (script, scenes, narration audio) once, offline | Cheap, reliable, smooth playback; narration is cached and replayed for every student |
| 2 | Only question handling runs live | Keeps cost and latency low |
| 3 | Code owns the lesson state (a "lesson pointer" / current scene), not the LLM | LLMs drift and lose their place; code decides when to resume |
| 4 | "Drawing" = LLM writes draw actions (text, arrows, shapes) per narration sentence; the player animates strokes | LLM-drawn pixels are unreliable; stroke animation looks like live drawing |
| 5 | Sync drawing to audio by generating TTS per sentence and using each clip's duration | Simple and robust |
| 6 | Questions use lookahead: if covered in upcoming scenes → queue + promise; if not → answer at the end of the current scene | Hides latency and keeps the lesson on track |
| 7 | Use pre-recorded filler lines ("Accha sawal!") | Instant, zero cost |
| 8 | Use a cascade (STT → LLM → TTS), not a speech-to-speech model | The answer text is needed for the board; easier to control |
| 9 | Use Sarvam for everything (STT + TTS), same voice for narration and answers | Voice stays consistent; good Hinglish; cheaper than ElevenLabs |
| 10 | ElevenLabs only as a backup if Sarvam voice quality disappoints | ~3x costlier; free tier is ~1 lecture/month, non-commercial |
| 11 | Push-to-talk for the demo instead of auto voice detection | Background noise causes false triggers |
| 12 | Whole transcript in the prompt for the demo; no embeddings yet | One topic's transcript is small enough |
| 13 | Fetch transcripts locally, not on a cloud server | YouTube blocks most cloud IPs |

## 3. Architecture

### Offline prep (once per lecture)

1. YouTube video → transcript
2. LLM converts it into a script and scenes: each step is `{say: "...", draw: [...]}`
3. TTS per sentence → audio files plus their durations
4. Scene package (JSON + audio), cached

### Live session

1. Player plays audio and animates the board scene by scene.
2. Student presses the mic button → Sarvam STT.
3. Question handler classifies intent and looks ahead in the upcoming scenes:
   - **Covered later:** "Yeh hum agle 2 minute mein dekhenge", store the question, and refer back to it when that scene arrives ("Tumne pucha tha…").
   - **Not covered:** "Is point ko pehle khatam karte hain", keep playing; generate the answer and its audio in the background, then play it at the scene's end.
   - **Repeat / slow down:** re-explain with a new example.
   - **Off-topic:** brief reply, redirect.
4. Log the doubt to the student profile and resume the scene where it paused.

### Real-time answer playback

1. LLM outputs one JSON object per line (`{"say": ..., "draw": [...]}`) so it can be parsed while still being written.
2. As soon as the first line arrives, send it to TTS and start its drawing.
3. Demo: regular per-sentence TTS (gives exact duration); generate sentence 2 while sentence 1 plays.
4. Later: Sarvam WebSocket streaming TTS if gaps between sentences are noticeable.

Approximate time to the first word of an answer: ~2 s (STT ~0.5 s, LLM first sentence ~1 s, TTS first audio ~0.25 s), covered by the filler line or the rest of the current scene.

## 4. Data needed

**Lesson store (per topic):**
- Ordered scenes/steps: narration, draw actions, one example, one check question with answer
- Key terms, common misconceptions

**Student profile (keep minimal; users are minors):**
- Name, class, board (CBSE/RBSE), language (Hindi / English / Hinglish)
- Per session: doubts asked, check questions right/wrong
- Weak concepts list, used in the next session

## 5. LLM tools

- `get_step(n)`: fetch a lesson step
- `search_transcript(query)`: find relevant chunks for a doubt (later, when topics get long)
- `ask_check_question()` and `record_answer(concept, correct)`
- `update_profile(weak_concept)`
- `end_session_summary()`: recap and what to revise

The demo needs only `get_step`, `record_answer` and `end_session_summary`.

## 6. Transcript options

| Option | Cost | Use when |
|---|---|---|
| `youtube-transcript-api` (Python, no download) | Free | The video has manually uploaded captions; run locally (cloud IPs get blocked) |
| `yt-dlp` audio + Sarvam STT | ~₹5–15 per lecture | Captions are auto-generated Hindi or missing; best quality |
| YouTube Studio subtitle download | Free | Your own channel's videos; most reliable, but manual |

Recommendation: try the library first and check `transcript.is_generated`. If it's auto-generated Hindi, switch to `yt-dlp` + Sarvam STT.

## 7. Stack and costing (demo)

| Part | Tool | Cost |
|---|---|---|
| Transcript | `youtube-transcript-api`, or `yt-dlp` + Sarvam STT | ₹0–15 per lecture |
| Script + scenes | LLM (Sarvam-30B or Gemini free tier) | ~₹0–2 per lecture |
| Narration audio | Sarvam Bulbul, generated once | ₹15–30 per 10-min lecture (~10k characters) |
| Student question STT | Sarvam Saaras | paise per question |
| Answer text + draw actions | Same LLM | paise |
| Answer audio | Same Bulbul voice | under ₹1 per question |
| Player hosting | Static web page | Free |

Reference rates (checked Oct 2026):
- Sarvam: ₹100 free credits for new accounts (reduced from ₹1,000); STT ₹30/hour; TTS ₹15 per 10k characters (Bulbul v2), ₹30 (v3).
- ElevenLabs: $0.05 per 1k characters (Flash), $0.10 (Multilingual v2/v3); free plan 10k characters/month, non-commercial.
- Maya1 (Maya Research): open-source (Apache 2.0), needs a ~16 GB GPU; Veena is their older Hindi/English model.

**Total for a demo with 3–5 topics plus testing: about ₹150–300.** The free ₹100 Sarvam credit covers roughly the first topic. Narration is cached, so more viewers don't add cost; only questions do.

## 8. Feasibility

**Doable in the demo**
- Whiteboard scenes: text, bullets, arrows, boxes, simple labelled shapes, flows, equations, number lines, simple graphs
- Hindi/Hinglish narration
- Questions with the lookahead/queue trick, answered by voice plus a text card
- Resuming the scene exactly where it paused

**Possible but weaker**
- Drawing new content live for an answer: keep it to text and arrows, or highlight existing board elements
- Complex science diagrams (heart, eye, circuits): use pre-made images, animate labels and arrows on top
- Maps and geometry constructions: need templates, not free-form LLM drawing

**Not doable for a demo or for free**
- Instant (<1 s) answers with a fresh custom drawing (hence the time-buying trick)
- Realistic lip-synced teacher avatar (use an icon or pen cursor)
- Accurate answers far outside the transcript (redirect instead)
- Zero cost at scale
- Clean speech recognition in noisy classrooms

## 9. Known quirks to handle

- Latency of the chained pipeline: stream sentence by sentence
- Hinglish STT may come out in the wrong script: tell the LLM to expect messy input
- TTS reading formulas ("H₂O", "x²", "3/4"): the LLM must write speakable text ("x square", "teen by chaar")
- Long answers: limit to 2–3 sentences, then a question back to the student
- Hallucination: answer only from the transcript, otherwise "yeh hum aage padhenge"

## 10. Build plan (~1.5–2 weeks, one person)

- Prep pipeline (transcript → scene JSON → audio): 2–3 days
- Player with synced drawing animation: 3–4 days
- Question handling (STT, lookahead, queue, background answer): 2–3 days

## 11. Next steps

- Pick one YouTube lecture for the demo topic
- Generate its scene JSON (format to be finalised from a real transcript)
- Test Sarvam voice quality for the tutor voice; keep ElevenLabs as a fallback
