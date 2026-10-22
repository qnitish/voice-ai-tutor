"""Offline lesson prep: lecture audio -> transcript -> scenes -> narration audio -> Postgres.

    python -m app.prep <audio-file> --slug biot-savart [--start-min 0] [--minutes 10] [--redo STAGE]

Each stage caches its output in data/lessons/<slug>/ and is skipped when rerun;
--redo STAGE deletes that stage's cache and every later one.
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path

from app.audio import speak, speak_cached, split_for_stt
from app.config import settings
from app.db import Base, SessionLocal, engine
from app.lesson_store import publish_lesson
from app import llm
from app.prompts import BRIDGES, FIXED_ACKS, OUTLINE_SYSTEM, SCENE_SYSTEM
from app.providers import get_stt
from app.schemas import Outline, SceneDraft, clean_draw

log = logging.getLogger("prep")

STAGES = ["transcript", "outline", "scenes", "audio"]
FILLERS = [
    "Accha sawal!",
    "Hmm, interesting sawal hai.",
    "Ek second, sochne do.",
    "Haan, bolo, main sun rahi hoon.",
]


async def bounded(sem: asyncio.Semaphore, coro):
    async with sem:
        return await coro


async def make_transcript(audio: Path, start_min: float, minutes: float) -> str:
    stt = get_stt()
    chunk_s = stt.max_seconds - 2
    chunks = split_for_stt(audio, start_min * 60, minutes * 60, chunk_s)
    log.info("transcribing %d chunks of %ss", len(chunks), chunk_s)
    sem = asyncio.Semaphore(4)
    texts = await asyncio.gather(*(bounded(sem, stt.transcribe(c)) for c in chunks))
    lines = []
    for i, text in enumerate(texts):
        t = int(start_min * 60 + i * chunk_s)
        lines.append(f"[{t // 60:02d}:{t % 60:02d}] {text}")
    return "\n".join(lines)


async def make_scene(outline: Outline, idx: int, transcript: str, earlier: list[SceneDraft]) -> SceneDraft:
    taught = "\n".join(
        f"Scene {i}: " + " ".join(s.say for s in scene.steps) for i, scene in enumerate(earlier)
    )
    user = (
        f"Lesson outline:\n{outline.model_dump_json(indent=1)}\n\n"
        f"Already taught in earlier scenes:\n{taught or '(nothing yet, this is the first scene)'}\n\n"
        f"Write scene index {idx}: {outline.scenes[idx].model_dump_json()}\n\n"
        f"Lecture transcript:\n{transcript}"
    )
    draft = await llm.complete_json(SCENE_SYSTEM, user, SceneDraft)
    for step in draft.steps:
        step.draw = clean_draw(step.draw)
    return draft


async def narrate(slug: str, scenes: list[SceneDraft]) -> list[dict]:
    """TTS every step and check question; returns scenes as dicts with audio attached."""
    sem = asyncio.Semaphore(4)

    async def scene_audio(si: int, scene: SceneDraft) -> dict:
        spoken = await asyncio.gather(
            *(bounded(sem, speak(s.say, f"lessons/{slug}/s{si}_{i}.wav")) for i, s in enumerate(scene.steps)),
            bounded(sem, speak(scene.check_question.say, f"lessons/{slug}/s{si}_check.wav")),
        )
        steps = [
            {**s.model_dump(), "audio_url": a.audio_url, "duration_ms": a.duration_ms}
            for s, a in zip(scene.steps, spoken[:-1])
        ]
        check = {**scene.check_question.model_dump(), "audio_url": spoken[-1].audio_url}
        return {"steps": steps, "check_question": check}

    return await asyncio.gather(*(scene_audio(i, s) for i, s in enumerate(scenes)))


async def make_fillers() -> None:
    """Pre-generate the lines played instantly during live questions and after answers."""
    for text in FIXED_ACKS.values():
        await speak_cached(text)
    for name, lines in (("fillers", FILLERS), ("bridges", BRIDGES)):
        manifest = settings.media_dir / "fillers" / f"{name}.json"
        if manifest.exists():
            continue
        spoken = await asyncio.gather(*(speak(t, f"fillers/{name}_{i}.wav") for i, t in enumerate(lines)))
        manifest.write_text(json.dumps([s.model_dump() for s in spoken], ensure_ascii=False, indent=1))


def load_db(slug: str, outline: Outline, transcript: str, scenes: list[dict]) -> int:
    """Publish as a new lesson version; returns the version number."""
    Base.metadata.create_all(engine)
    rows = [
        {"title": meta.title, "goal": meta.goal, "key_terms": meta.key_terms, **scene}
        for meta, scene in zip(outline.scenes, scenes)
    ]
    with SessionLocal.begin() as db:
        return publish_lesson(db, slug, outline.title, transcript, rows).version


async def run(args: argparse.Namespace) -> None:
    out = settings.lessons_dir / args.slug
    out.mkdir(parents=True, exist_ok=True)
    files = {s: out / f"{s}.json" for s in STAGES}
    if args.redo:
        for stage in STAGES[STAGES.index(args.redo) :]:
            files[stage].unlink(missing_ok=True)

    async def cached(stage: str, make):
        if files[stage].exists():
            log.info("%s: cached", stage)
            return json.loads(files[stage].read_text())
        log.info("%s: generating", stage)
        value = await make()
        files[stage].write_text(json.dumps(value, ensure_ascii=False, indent=1))
        return value

    transcript = await cached(
        "transcript", lambda: make_transcript(Path(args.audio), args.start_min, args.minutes)
    )

    async def outline_json():
        return (await llm.complete_json(OUTLINE_SYSTEM, transcript, Outline)).model_dump()

    outline = Outline.model_validate(await cached("outline", outline_json))

    async def scenes_json():
        # Sequential, so each scene knows what earlier scenes already taught
        drafts: list[SceneDraft] = []
        for i in range(len(outline.scenes)):
            drafts.append(await make_scene(outline, i, transcript, drafts))
        return [d.model_dump() for d in drafts]

    drafts = [SceneDraft.model_validate(d) for d in await cached("scenes", scenes_json)]
    scenes = await cached("audio", lambda: narrate(args.slug, drafts))
    await make_fillers()
    version = load_db(args.slug, outline, transcript, scenes)
    steps = sum(len(s["steps"]) for s in scenes)
    log.info(
        "published lesson '%s' (%s v%d): %d scenes, %d steps", outline.title, args.slug, version, len(scenes), steps
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("audio")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--start-min", type=float, default=0)
    parser.add_argument("--minutes", type=float, default=10)
    parser.add_argument("--redo", choices=STAGES)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
