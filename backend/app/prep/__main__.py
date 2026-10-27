"""Offline lesson prep: lecture audio -> transcript -> scenes -> narration audio -> remediation -> Postgres.

    python -m app.prep <audio-or-video-file> --slug biot-savart [--start-min 0] [--minutes 10] [--redo STAGE] [--voice NAME]
        [--language hinglish|english] [--lecture-language hindi|english] [--share-from SLUG] [--label TEXT]
        [--renderer svg|skia]

Each stage caches its output in data/lessons/<slug>/ and is skipped when rerun;
--redo STAGE deletes that stage's cache and every later one. --voice uses a voice from app.voices (e.g. a clone)
for everything spoken in the lesson; changing it needs --redo scenes, since the narration's verbs follow its gender.
--language is what the tutor speaks; --lecture-language is what the recording is in (picks the STT language).
--share-from copies another slug's transcript and outline (e.g. the same lecture in the other language), so both
versions teach the same scenes and the lecture is transcribed once. --label is a short build note the app shows as a tag.
--renderer picks the app's whiteboard (svg, or skia via CanvasKit) for comparing the two.
"""

import argparse
import asyncio
import json
import logging
import re
from pathlib import Path

from app.audio import speak, speak_cached, split_for_stt
from app.config import settings
from app.db import SessionLocal, migrate
from app.lesson_store import publish_lesson
from app import llm
from app.prompts import (
    BRIDGES,
    CHECK_FOLLOWUP,
    CHECK_LINES,
    FILLERS,
    FIXED_ACKS,
    OUTLINE_SYSTEM,
    REMEDY_REVIEW_SYSTEM,
    REMEDY_SYSTEM,
    REVIEW_SYSTEM,
    SCENE_SYSTEM,
    for_gender,
    line_for,
)
from app.language import current_stt_language
from app.providers import get_stt
from app.schemas import (
    CheckReview,
    Outline,
    OutlineScene,
    RemedyDraft,
    RemedyReview,
    SceneDraft,
    clean_draw,
    leaks_answer,
)
from app.voices import fillers_dir, get_voice

log = logging.getLogger("prep")

STAGES = ["transcript", "outline", "scenes", "review", "audio", "remedy"]
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


async def make_scene(
    outline: Outline,
    idx: int,
    transcript: str,
    earlier: list[SceneDraft],
    voice: str | None = None,
    language: str = "hinglish",
) -> SceneDraft:
    taught = "\n".join(
        f"Scene {i}: " + " ".join(s.say for s in scene.steps) for i, scene in enumerate(earlier)
    )
    user = (
        f"Lesson outline:\n{outline.model_dump_json(indent=1)}\n\n"
        f"Already taught in earlier scenes:\n{taught or '(nothing yet, this is the first scene)'}\n\n"
        f"Write scene index {idx}: {outline.scenes[idx].model_dump_json()}\n\n"
        f"Lecture transcript:\n{transcript}"
    )
    draft = await llm.prep_json(for_gender(SCENE_SYSTEM, get_voice(voice).gender, language), user, SceneDraft)
    for step in draft.steps:
        step.draw = clean_draw(step.draw)
    return draft


def check_text(q) -> str:
    return (
        f"Check question: {q.question}\n"
        + "".join(f"Option {i}: {o}\n" for i, o in enumerate(q.options))
        + f"Correct option index: {q.correct_index}\nExplanation: {q.explanation}"
    )


async def review_scene(
    meta: OutlineScene, scene: SceneDraft, transcript: str, voice: str | None = None, language: str = "hinglish"
) -> dict:
    """Fact-check one scene against the lecture before it is narrated; returns the (corrected) check question."""
    user = (
        f"Scene: {meta.title}\nGoal: {meta.goal}\n"
        + "".join(f"Step {i}: {s.say}\n" for i, s in enumerate(scene.steps))
        + f"\n{check_text(scene.check_question)}\n\n"
        f"Check question as JSON: {scene.check_question.model_dump_json()}\n\n"
        f"Lecture transcript:\n{transcript}"
    )
    review = await llm.prep_json(for_gender(REVIEW_SYSTEM, get_voice(voice).gender, language), user, CheckReview)
    for issue in review.issues + review.narration_issues:
        log.warning("review %s: %s", meta.title, issue)
    return review.model_dump()


async def narrate(slug: str, scenes: list[SceneDraft], voice: str | None = None) -> list[dict]:
    """TTS every step and check question; returns scenes as dicts with audio attached."""
    sem = asyncio.Semaphore(4)

    async def scene_audio(si: int, scene: SceneDraft) -> dict:
        spoken = await asyncio.gather(
            *(bounded(sem, speak(s.say, f"lessons/{slug}/s{si}_{i}.wav", voice)) for i, s in enumerate(scene.steps)),
            bounded(sem, speak(scene.check_question.say, f"lessons/{slug}/s{si}_check.wav", voice)),
        )
        steps = [
            {**s.model_dump(), "audio_url": a.audio_url, "duration_ms": a.duration_ms}
            for s, a in zip(scene.steps, spoken[:-1])
        ]
        check = {**scene.check_question.model_dump(), "audio_url": spoken[-1].audio_url}
        return {"steps": steps, "check_question": check}

    return await asyncio.gather(*(scene_audio(i, s) for i, s in enumerate(scenes)))


# A reviewer that talks about its sources ("lecture mein nahi bataya") has written for us, not the student
META = re.compile(r"\b(lecture|transcript|review)\b", re.I)


def merge_review(draft: RemedyDraft, reviewed: RemedyDraft, title: str) -> RemedyDraft:
    """Take the reviewer's corrections, except parts where it leaked meta commentary into spoken text."""
    hints = {h.option: h for h in draft.why_wrong}
    for h in reviewed.why_wrong:
        if META.search(h.hint):
            log.warning("remedy %s: kept the original hint for option %d (review talked about its sources)", title, h.option)
        else:
            hints[h.option] = h
    reteach = reviewed.reteach
    if any(META.search(s.say) for s in reteach):
        log.warning("remedy %s: kept the original re-teach (review talked about its sources)", title)
        reteach = draft.reteach
    twin = reviewed.twin
    if META.search(f"{twin.say} {twin.question} {twin.explanation}"):
        log.warning("remedy %s: kept the original twin (review talked about its sources)", title)
        twin = draft.twin
    return draft.model_copy(update={"why_wrong": list(hints.values()), "reteach": reteach, "twin": twin})


async def make_remedy(
    meta: OutlineScene, scene: SceneDraft, voice: str | None = None, transcript: str = "", language: str = "hinglish"
) -> RemedyDraft:
    """Hints per wrong option, a re-teach and a twin question for one scene's check question, then reviewed."""
    q = scene.check_question
    gender = get_voice(voice).gender
    context = (
        f"Scene: {meta.title}\nGoal: {meta.goal}\nCommon misconceptions: {'; '.join(meta.misconceptions) or 'none given'}\n"
        f"Narration: {' '.join(s.say for s in scene.steps)}\n\n{check_text(q)}\n\n"
        f"Lecture transcript:\n{transcript or '(not given)'}"
    )
    draft = await llm.prep_json(for_gender(REMEDY_SYSTEM, gender, language), context, RemedyDraft)
    review = await llm.prep_json(
        for_gender(REMEDY_REVIEW_SYSTEM, gender, language), f"{context}\n\nHelp to review:\n{draft.model_dump_json()}", RemedyReview
    )
    for issue in review.issues:
        log.warning("remedy review %s: %s", meta.title, issue)
    draft = merge_review(draft, review.remedy, meta.title)
    # Last line of defence: a hint quoting the right answer would make the retry meaningless
    correct = q.options[q.correct_index]
    hints = [h for h in draft.why_wrong if not leaks_answer(h.hint, correct)]
    if len(hints) < len(draft.why_wrong):
        log.warning("remedy %s: dropped %d hint(s) that give the answer away", meta.title, len(draft.why_wrong) - len(hints))
    reteach = [s.model_copy(update={"draw": clean_draw(s.draw)}) for s in draft.reteach]
    return draft.model_copy(update={"why_wrong": hints, "reteach": reteach})


async def remediate(
    slug: str,
    outline: Outline,
    scenes: list[SceneDraft],
    voice: str | None = None,
    transcript: str = "",
    language: str = "hinglish",
) -> list[dict]:
    """Write and narrate the wrong-answer help of every scene; merged into its check question on publish."""
    sem = asyncio.Semaphore(4)
    drafts = await asyncio.gather(
        *(bounded(sem, make_remedy(m, s, voice, transcript, language)) for m, s in zip(outline.scenes, scenes))
    )

    async def scene_audio(si: int, d: RemedyDraft) -> dict:
        # Parallel to the options; the correct option (and any option the LLM skipped) gets no hint
        text = {h.option: h.hint for h in d.why_wrong if h.option != scenes[si].check_question.correct_index}

        async def hint(j: int):
            return (await speak(text[j], f"lessons/{slug}/s{si}_why{j}.wav", voice)).model_dump() if text.get(j) else None

        why_wrong, reteach, twin = await asyncio.gather(
            asyncio.gather(*(bounded(sem, hint(j)) for j in range(len(scenes[si].check_question.options)))),
            asyncio.gather(*(bounded(sem, speak(s.say, f"lessons/{slug}/s{si}_re{i}.wav", voice)) for i, s in enumerate(d.reteach))),
            bounded(sem, speak(d.twin.say, f"lessons/{slug}/s{si}_twin.wav", voice)),
        )
        return {
            "why_wrong": why_wrong,
            "reteach": [
                {**s.model_dump(), "audio_url": a.audio_url, "duration_ms": a.duration_ms} for s, a in zip(d.reteach, reteach)
            ],
            "twin": {**d.twin.model_dump(), "audio_url": twin.audio_url},
            "followup": (await speak_cached(line_for(CHECK_FOLLOWUP, get_voice(voice).gender, language), voice)).model_dump(),
        }

    return list(await asyncio.gather(*(scene_audio(i, d) for i, d in enumerate(drafts))))


async def make_fillers(voice: str | None = None, language: str = "hinglish") -> None:
    """Pre-generate the lines played instantly during live questions and after answers."""
    gender = get_voice(voice).gender
    for text in [*FIXED_ACKS.values(), *CHECK_LINES.values(), CHECK_FOLLOWUP]:
        await speak_cached(line_for(text, gender, language), voice)
    folder = fillers_dir(voice, language)
    folder.mkdir(parents=True, exist_ok=True)
    rel = folder.relative_to(settings.media_dir).as_posix()
    for name, lines in (("fillers", FILLERS), ("bridges", BRIDGES)):
        manifest = folder / f"{name}.json"
        if manifest.exists():
            continue
        spoken = await asyncio.gather(
            *(speak(line_for(t, gender, language), f"{rel}/{name}_{i}.wav", voice) for i, t in enumerate(lines))
        )
        manifest.write_text(json.dumps([s.model_dump() for s in spoken], ensure_ascii=False, indent=1))


def load_db(
    slug: str,
    outline: Outline,
    transcript: str,
    scenes: list[dict],
    remedies: list[dict],
    voice: str | None = None,
    language: str = "hinglish",
    label: str | None = None,
    renderer: str = "svg",
) -> int:
    """Publish as a new lesson version; returns the version number."""
    migrate()
    rows = [
        {
            "title": meta.title,
            "goal": meta.goal,
            "key_terms": meta.key_terms,
            **scene,
            "check_question": {**scene["check_question"], **remedy},
        }
        for meta, scene, remedy in zip(outline.scenes, scenes, remedies)
    ]
    with SessionLocal.begin() as db:
        return publish_lesson(db, slug, outline.title, transcript, rows, voice, language, label, renderer).version


async def run(args: argparse.Namespace) -> None:
    out = settings.lessons_dir / args.slug
    out.mkdir(parents=True, exist_ok=True)
    files = {s: out / f"{s}.json" for s in STAGES}
    if args.share_from:
        for stage in ("transcript", "outline"):
            src = settings.lessons_dir / args.share_from / f"{stage}.json"
            if not src.exists():
                raise SystemExit(f"--share-from: {src} not found; prepare {args.share_from} first")
            if not files[stage].exists():
                files[stage].write_text(src.read_text())
    if args.redo:
        for stage in STAGES[STAGES.index(args.redo) :]:
            files[stage].unlink(missing_ok=True)
    if not files["review"].exists() and files["audio"].exists():
        # Prepared before the review stage existed: re-narrate from the reviewed check questions
        log.info("no review yet: redoing audio and remedy after it")
        files["audio"].unlink()
        files["remedy"].unlink(missing_ok=True)

    async def cached(stage: str, make):
        if files[stage].exists():
            log.info("%s: cached", stage)
            return json.loads(files[stage].read_text())
        log.info("%s: generating", stage)
        value = await make()
        files[stage].write_text(json.dumps(value, ensure_ascii=False, indent=1))
        return value

    current_stt_language.set(args.lecture_language)
    transcript = await cached(
        "transcript", lambda: make_transcript(Path(args.audio), args.start_min, args.minutes)
    )

    async def outline_json():
        return (await llm.prep_json(OUTLINE_SYSTEM, transcript, Outline)).model_dump()

    outline = Outline.model_validate(await cached("outline", outline_json))

    async def scenes_json():
        # Sequential, so each scene knows what earlier scenes already taught
        drafts: list[SceneDraft] = []
        for i in range(len(outline.scenes)):
            drafts.append(await make_scene(outline, i, transcript, drafts, args.voice, args.language))
        return [d.model_dump() for d in drafts]

    drafts = [SceneDraft.model_validate(d) for d in await cached("scenes", scenes_json)]

    async def review_json():
        sem = asyncio.Semaphore(4)
        return list(
            await asyncio.gather(
                *(bounded(sem, review_scene(m, d, transcript, args.voice, args.language)) for m, d in zip(outline.scenes, drafts))
            )
        )

    # Narration and remediation use the reviewed check questions
    reviews = await cached("review", review_json)
    drafts = [d.model_copy(update={"check_question": CheckReview.model_validate(r).check_question}) for d, r in zip(drafts, reviews)]
    flagged = [(m.title, i) for m, r in zip(outline.scenes, reviews) for i in r["narration_issues"]]
    scenes = await cached("audio", lambda: narrate(args.slug, drafts, args.voice))
    remedies = await cached("remedy", lambda: remediate(args.slug, outline, drafts, args.voice, transcript, args.language))
    await make_fillers(args.voice, args.language)
    version = load_db(args.slug, outline, transcript, scenes, remedies, args.voice, args.language, args.label, args.renderer)
    steps = sum(len(s["steps"]) for s in scenes)
    log.info(
        "published lesson '%s' (%s v%d): %d scenes, %d steps", outline.title, args.slug, version, len(scenes), steps
    )
    if flagged:
        # The reviewer only corrects check questions; narration problems need a person (edit scenes.json, --redo review)
        log.warning("%d narration problem(s) to check by hand (see review.json):", len(flagged))
        for title, issue in flagged:
            log.warning("  %s: %s", title, issue)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("audio")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--start-min", type=float, default=0)
    parser.add_argument("--minutes", type=float, default=10)
    parser.add_argument("--redo", choices=STAGES)
    parser.add_argument("--voice", help="voice name from data/voices/ (default: the configured stock voice)")
    parser.add_argument("--language", choices=["hinglish", "english"], default="hinglish", help="what the tutor speaks")
    parser.add_argument(
        "--lecture-language", choices=["hindi", "english"], default="hindi", help="language of the recording (for STT)"
    )
    parser.add_argument("--share-from", help="reuse this slug's transcript and outline")
    parser.add_argument("--renderer", choices=["svg", "skia"], default="svg", help="whiteboard the app draws it with")
    parser.add_argument("--label", help="short build note shown as a tag in the app, e.g. 'Pause rules'")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
