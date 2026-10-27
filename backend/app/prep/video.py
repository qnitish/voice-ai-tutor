"""Offline prep of a video lesson: the lecture video plays as is; prep indexes it into chapters and writes the
checkpoint questions asked at pauses (docs/plan/video-lesson-spec.md, 2.1).

    python -m app.prep.video <video-file> --slug lecture-05-video [--voice NAME] [--language english]
        [--lecture-language english] [--share-from SLUG] [--label TEXT] [--redo STAGE]

Stages, each cached as JSON in data/lessons/<slug>/ and skipped when rerun (--redo STAGE deletes that stage's
cache and every later one): video (encode for the browser), transcript, chapters, checkpoints (where to pause, and
the questions), review (fact-check), remedy (hints per wrong option, the teacher's replay range), audio (TTS).
--share-from copies another slug's transcript (e.g. the scene lesson of the same lecture).
The fixed lines the live routes play (acks, thread lines, template recaps) are pre-generated at the end; for a lesson
that is already published, `python -m app.prep.video --slug SLUG --lines-only` does only that (in the lesson's voice).
"""

import argparse
import asyncio
import json
import logging
import re
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from app.audio import cached_path, speak, speak_cached
from app.config import settings
from app.db import SessionLocal, migrate
from app.language import current_stt_language
from app.lesson_store import latest_lesson, publish_video_lesson
from app import llm
from app.prep.__main__ import bounded, check_text, make_fillers, make_transcript
from app.prompts import (
    CHAPTERS_SYSTEM,
    CHECKPOINT_FOLLOWUP,
    CHECKPOINT_QUESTIONS_SYSTEM,
    CHECKPOINT_REMEDY_SYSTEM,
    CHECKPOINT_REVIEW_SYSTEM,
    CHECKPOINTS_SYSTEM,
    VIDEO_ACK_COVERED_NOW,
    VIDEO_ACK_NEUTRAL,
    VIDEO_ACKS,
    VIDEO_CAPPED_LINE,
    VIDEO_FOLLOWUP_LINE,
    VIDEO_RECAP_TEMPLATE,
    VIDEO_SORRY_LINE,
    for_gender,
)
from app.quiz import shuffle_checks
from app.schemas import DEVANAGARI, CheckpointQuestion, CheckQuestion, CheckReview, OptionHint, leaks_answer
from app.voices import get_voice

log = logging.getLogger("prep.video")

STAGES = ["video", "transcript", "chapters", "checkpoints", "review", "remedy", "audio"]
REPLAY_MAX_S = 90

# ---- LLM drafts ----

# Spoken text must not point at an option by letter or position: the options are shuffled on publish
OPTION_REF = re.compile(r"\boption\s+(?:[a-d]|[1-4]|one|two|three|four)\b|\b(?:first|second|third|fourth|last)\s+option\b", re.I)


def names_option(text: str) -> bool:
    return bool(OPTION_REF.search(text))


class ChapterDraft(BaseModel):
    topic: str
    title: str
    summary: str
    key_terms: list[str] = []
    misconceptions: list[str] = []
    start_s: float
    end_s: float


class ChaptersDraft(BaseModel):
    title: str
    chapters: list[ChapterDraft] = Field(min_length=1)


class PlanItem(BaseModel):
    after_chapter: int
    questions: int = 1
    reason: str = ""


class CheckpointPlan(BaseModel):
    plan: list[PlanItem]


class CheckpointDraft(BaseModel):
    """The questions of one checkpoint and the line that introduces them."""

    intro: str
    questions: list[CheckQuestion] = Field(min_length=1)

    @model_validator(mode="after")
    def spoken_ok(self):
        spoken = [self.intro] + [q.say for q in self.questions]
        if any(DEVANAGARI.search(t) for t in spoken):
            raise ValueError('every "say" must be in Roman script, not Devanagari')
        if any(names_option(q.say) for q in self.questions):
            raise ValueError('"say" must not name an option letter or position; the options are shuffled later')
        return self


class CheckpointRemedyDraft(BaseModel):
    """Hints per wrong option and where in the video the teacher explains the concept."""

    why_wrong: list[OptionHint]
    replay_from_s: float
    replay_to_s: float

    @model_validator(mode="after")
    def spoken_ok(self):
        if any(DEVANAGARI.search(h.hint) for h in self.why_wrong):
            raise ValueError("every hint must be in Roman script, not Devanagari")
        if any(names_option(h.hint) for h in self.why_wrong):
            raise ValueError("a hint must not name an option letter or position; the options are shuffled later")
        return self


# ---- pure helpers (tested) ----


def fix_chapters(chapters: list[dict], duration_s: float) -> list[dict]:
    """Make the LLM's chapter times contiguous: sorted, starting at 0, each ending where the next starts."""
    ordered = sorted(chapters, key=lambda c: c["start_s"])
    out = []
    for i, c in enumerate(ordered):
        start = 0 if i == 0 else c["start_s"]
        end = ordered[i + 1]["start_s"] if i + 1 < len(ordered) else duration_s
        if end > start:
            out.append({**c, "start_s": start, "end_s": end})
    if not out:
        raise ValueError("no chapter with a positive length")
    return out


def plan_checkpoints(
    plan: list[dict], chapters: list[dict], duration_s: float, min_gap_s: float, max_n: int, max_q: int
) -> list[dict]:
    """Turn the LLM's plan into checkpoints at chapter ends, spaced at least min_gap_s apart, at most max_n."""
    items = []
    seen = set()
    for p in plan:
        idx = p["after_chapter"]
        if not 0 <= idx < len(chapters) or idx in seen:
            continue
        seen.add(idx)
        t_s = chapters[idx]["end_s"]
        if t_s < 60:
            continue
        items.append(
            {
                "chapter_idx": idx,
                "t_s": t_s,
                "questions": min(max(int(p.get("questions", 1)), 1), max_q),
                "reason": p.get("reason", ""),
            }
        )
    items.sort(key=lambda c: c["t_s"])
    out: list[dict] = []
    for item in items:
        if out and item["t_s"] - out[-1]["t_s"] < min_gap_s:
            continue
        out.append(item)
    return out[:max_n]


def snap_to_silence(t_s: float, silences: list[tuple[float, float]], window_s: float) -> float:
    """Move a pause point into the nearest silence (its midpoint) within window_s, so the teacher isn't cut mid-word."""
    best = None
    for start, end in silences:
        mid = (start + end) / 2
        dist = abs(mid - t_s)
        if dist <= window_s and (best is None or dist < best[0]):
            best = (dist, mid)
    return t_s if best is None else best[1]


SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
SILENCE_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def parse_silences(text: str) -> list[tuple[float, float]]:
    """(start, end) pairs from ffmpeg silencedetect output; a final silence without an end is dropped."""
    out = []
    start = None
    for line in text.splitlines():
        if m := SILENCE_START.search(line):
            start = float(m.group(1))
        elif (m := SILENCE_END.search(line)) and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    return out


# ---- media ----


def ffprobe_duration(path: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)]
    return float(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip())


def encode_video(src: Path, slug: str) -> dict:
    """Encode the lecture for the browser (H.264 ≤ 1280x720, AAC); kept if it already exists."""
    out = settings.media_dir / "videos" / f"{slug}.mp4"
    if out.exists():
        log.info("video: %s exists, not re-encoding", out)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        log.info("video: encoding %s -> %s", src, out)
        # Fit inside 1280x720 without upscaling; even dimensions and yuv420p so every browser decodes it
        scale = "scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2"
        cmd = [
            "ffmpeg", "-v", "error", "-y", "-i", str(src),
            "-c:v", "libx264", "-crf", "26", "-preset", "veryfast", "-vf", scale, "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out),
        ]
        subprocess.run(cmd, check=True)
    return {"url": f"/media/videos/{slug}.mp4", "duration_ms": round(ffprobe_duration(out) * 1000)}


def detect_silences(path: Path) -> list[tuple[float, float]]:
    cmd = ["ffmpeg", "-nostats", "-i", str(path), "-vn", "-af", "silencedetect=noise=-30dB:d=0.4", "-f", "null", "-"]
    return parse_silences(subprocess.run(cmd, capture_output=True, text=True).stderr)


# ---- transcript ----

MARK = re.compile(r"^\[(\d+):(\d\d)\]")


def transcript_between(transcript: str, start_s: float, end_s: float) -> str:
    """The transcript chunks that overlap [start_s, end_s); each chunk runs from its mark to the next one."""
    lines = transcript.splitlines()
    marks = [int(m.group(1)) * 60 + int(m.group(2)) if (m := MARK.match(l)) else None for l in lines]
    out = []
    keep = False
    for i, line in enumerate(lines):
        t = marks[i]
        if t is not None:  # a line without a mark continues the chunk before it
            following = [m for m in marks[i + 1 :] if m is not None]
            t_next = following[0] if following else float("inf")
            keep = t < end_s and t_next > start_s
        if keep:
            out.append(line)
    return "\n".join(out)


def mmss(t_s: float) -> str:
    t = int(t_s)
    return f"{t // 60:02d}:{t % 60:02d}"


# ---- LLM stages ----


def chapter_text(idx: int, c: dict) -> str:
    return (
        f"Chapter {idx} [{mmss(c['start_s'])}-{mmss(c['end_s'])}]: {c['title']} (topic: {c['topic']})\n"
        f"Summary: {c['summary']}\nKey terms: {', '.join(c['key_terms']) or 'none'}\n"
        f"Common misconceptions: {'; '.join(c['misconceptions']) or 'none given'}"
    )


async def make_chapters(transcript: str, duration_s: float) -> dict:
    draft = await llm.prep_json(CHAPTERS_SYSTEM, f"Lecture length: {mmss(duration_s)}\n\nTranscript:\n{transcript}", ChaptersDraft)
    chapters = fix_chapters([c.model_dump() for c in draft.chapters], duration_s)
    if len(chapters) < len(draft.chapters):
        log.warning("chapters: dropped %d empty chapter(s)", len(draft.chapters) - len(chapters))
    return {"title": draft.title, "chapters": chapters}


async def make_questions(
    cp: dict, chapters: list[dict], transcript: str, voice: str | None = None, language: str = "english"
) -> CheckpointDraft:
    """The questions and intro line of one checkpoint, about its chapter only."""
    c = chapters[cp["chapter_idx"]]
    n = cp["questions"]
    user = (
        f"{chapter_text(cp['chapter_idx'], c)}\n\nWrite {n} question(s). Why this checkpoint: {cp['reason'] or 'not given'}\n\n"
        f"Transcript of this chapter:\n{transcript_between(transcript, c['start_s'], c['end_s'])}\n\n"
        f"Whole lecture transcript (context only; ask only about this chapter):\n{transcript}"
    )
    system = for_gender(CHECKPOINT_QUESTIONS_SYSTEM, get_voice(voice).gender, language)
    draft = await llm.prep_json(system, user, CheckpointDraft)
    if len(draft.questions) != n:
        log.warning("checkpoint after chapter %d: asked for %d question(s), got %d", cp["chapter_idx"], n, len(draft.questions))
    return draft


async def make_checkpoints(
    chapters: list[dict], transcript: str, video: Path, duration_s: float, voice: str | None = None, language: str = "english"
) -> list[dict]:
    """Where the video pauses (chapter ends snapped to a silence) and the questions asked there."""
    user = (
        "Chapters:\n" + "\n\n".join(chapter_text(i, c) for i, c in enumerate(chapters)) + f"\n\nTranscript:\n{transcript}"
    )
    plan = await llm.prep_json(CHECKPOINTS_SYSTEM, user, CheckpointPlan)
    planned = plan_checkpoints(
        [p.model_dump() for p in plan.plan],
        chapters,
        duration_s,
        settings.checkpoint_min_gap_s,
        settings.checkpoint_max,
        settings.checkpoint_max_questions,
    )
    if len(planned) < len(plan.plan):
        log.info("checkpoints: kept %d of the %d planned", len(planned), len(plan.plan))
    silences = detect_silences(video)
    log.info("checkpoints: %d silences in the video", len(silences))
    for cp in planned:
        snapped = snap_to_silence(cp["t_s"], silences, settings.checkpoint_snap_window_s)
        cp["at_ms"] = round(1000 * min(snapped, duration_s - 0.5))
        log.info(
            "checkpoint after chapter %d '%s' at %s (%d question(s))",
            cp["chapter_idx"], chapters[cp["chapter_idx"]]["title"], mmss(cp["at_ms"] / 1000), cp["questions"],
        )
    sem = asyncio.Semaphore(4)
    drafts = await asyncio.gather(*(bounded(sem, make_questions(cp, chapters, transcript, voice, language)) for cp in planned))
    return [{**cp, "intro": d.intro, "questions": [q.model_dump() for q in d.questions]} for cp, d in zip(planned, drafts)]


async def review_question(
    c: dict, idx: int, q: CheckQuestion, transcript: str, voice: str | None = None, language: str = "english"
) -> dict:
    """Fact-check one checkpoint question against the lecture; returns the (corrected) question."""
    user = (
        f"{chapter_text(idx, c)}\n\n{check_text(q)}\nSpoken form: {q.say}\n\n"
        f"Question as JSON: {q.model_dump_json()}\n\nLecture transcript:\n{transcript}"
    )
    review = await llm.prep_json(for_gender(CHECKPOINT_REVIEW_SYSTEM, get_voice(voice).gender, language), user, CheckReview)
    for issue in review.issues:
        log.warning("review %s / %s: %s", c["title"], q.concept, issue)
    return review.model_dump()


async def review_checkpoints(
    checkpoints: list[dict], chapters: list[dict], transcript: str, voice: str | None = None, language: str = "english"
) -> list[list[dict]]:
    sem = asyncio.Semaphore(4)

    async def one(cp: dict) -> list[dict]:
        c = chapters[cp["chapter_idx"]]
        qs = [CheckQuestion.model_validate(q) for q in cp["questions"]]
        return list(
            await asyncio.gather(*(bounded(sem, review_question(c, cp["chapter_idx"], q, transcript, voice, language)) for q in qs))
        )

    return list(await asyncio.gather(*(one(cp) for cp in checkpoints)))


def clamp_replay(from_s: float, to_s: float, c: dict) -> dict:
    """The replay range inside its chapter and at most REPLAY_MAX_S long; the chapter start if the LLM's is unusable."""
    start = max(c["start_s"], from_s)
    end = min(c["end_s"], to_s)
    if end <= start:
        start = c["start_s"]
        end = c["end_s"]
    end = min(end, start + REPLAY_MAX_S)
    return {"start_ms": round(start * 1000), "end_ms": round(end * 1000)}


async def make_remedy(
    c: dict, idx: int, q: CheckQuestion, transcript: str, voice: str | None = None, language: str = "english"
) -> dict:
    """Hints per wrong option and the teacher's replay range for one checkpoint question."""
    user = (
        f"{chapter_text(idx, c)}\n\n{check_text(q)}\n\n"
        f"Transcript of this chapter:\n{transcript_between(transcript, c['start_s'], c['end_s'])}"
    )
    draft = await llm.prep_json(for_gender(CHECKPOINT_REMEDY_SYSTEM, get_voice(voice).gender, language), user, CheckpointRemedyDraft)
    # Last line of defence: a hint quoting the right answer would make the retry meaningless
    correct = q.options[q.correct_index]
    hints = [h for h in draft.why_wrong if h.option != q.correct_index and not leaks_answer(h.hint, correct)]
    if len(hints) < len(draft.why_wrong):
        log.warning("remedy %s / %s: dropped %d hint(s) that give the answer away", c["title"], q.concept, len(draft.why_wrong) - len(hints))
    replay = clamp_replay(draft.replay_from_s, draft.replay_to_s, c)
    if replay["start_ms"] != round(draft.replay_from_s * 1000) or replay["end_ms"] != round(draft.replay_to_s * 1000):
        log.info("remedy %s / %s: replay clamped to %s-%s", c["title"], q.concept, mmss(replay["start_ms"] / 1000), mmss(replay["end_ms"] / 1000))
    return {"why_wrong": [h.model_dump() for h in hints], "replay": replay}


async def remediate(
    checkpoints: list[dict], chapters: list[dict], transcript: str, voice: str | None = None, language: str = "english"
) -> list[list[dict]]:
    sem = asyncio.Semaphore(4)

    async def one(cp: dict) -> list[dict]:
        c = chapters[cp["chapter_idx"]]
        qs = [CheckQuestion.model_validate(q) for q in cp["questions"]]
        return list(await asyncio.gather(*(bounded(sem, make_remedy(c, cp["chapter_idx"], q, transcript, voice, language)) for q in qs)))

    return list(await asyncio.gather(*(one(cp) for cp in checkpoints)))


# ---- audio ----


async def narrate(slug: str, checkpoints: list[dict], remedies: list[list[dict]], voice: str | None = None) -> list[dict]:
    """TTS every intro, question, hint and follow-up; returns checkpoints in their published shape (schemas.CheckpointOut)."""
    sem = asyncio.Semaphore(4)
    followup = await speak_cached(CHECKPOINT_FOLLOWUP, voice)

    async def question_audio(ci: int, qi: int, q: dict, remedy: dict) -> dict:
        # Parallel to the options; the correct option (and any option without a hint) gets None
        text = {h["option"]: h["hint"] for h in remedy["why_wrong"]}

        async def hint(j: int):
            return (await speak(text[j], f"lessons/{slug}/cp{ci}_q{qi}_why{j}.wav", voice)).model_dump() if text.get(j) else None

        say, *why_wrong = await asyncio.gather(
            bounded(sem, speak(q["say"], f"lessons/{slug}/cp{ci}_q{qi}.wav", voice)),
            *(bounded(sem, hint(j)) for j in range(len(q["options"]))),
        )
        return {
            **q,
            "audio_url": say.audio_url,
            "duration_ms": say.duration_ms,
            "why_wrong": why_wrong,
            "replay": remedy["replay"],
            "reteach": [],
            "followup": followup.model_dump(),
        }

    async def checkpoint_audio(ci: int, cp: dict, remedy: list[dict]) -> dict:
        intro, *questions = await asyncio.gather(
            bounded(sem, speak(cp["intro"], f"lessons/{slug}/cp{ci}_intro.wav", voice)),
            *(question_audio(ci, qi, q, r) for qi, (q, r) in enumerate(zip(cp["questions"], remedy))),
        )
        return {"at_ms": cp["at_ms"], "chapter_idx": cp["chapter_idx"], "intro": intro.model_dump(), "questions": questions}

    return list(await asyncio.gather(*(checkpoint_audio(i, cp, r) for i, (cp, r) in enumerate(zip(checkpoints, remedies)))))


# ---- publish ----


def shuffle_options(checkpoints: list[dict], seed: str) -> list[dict]:
    """Shuffle every question's options in one pass, so the correct answer is spread over A-D across the lesson."""
    flat = [q for cp in checkpoints for q in cp["questions"]]
    shuffled = iter(shuffle_checks(flat, seed))
    return [{**cp, "questions": [next(shuffled) for _ in cp["questions"]]} for cp in checkpoints]


def load_db(
    slug: str,
    title: str,
    transcript: str,
    video: dict,
    chapters: list[dict],
    checkpoints: list[dict],
    voice: str | None = None,
    language: str = "english",
    label: str | None = None,
) -> int:
    """Publish as a new lesson version; returns the version number."""
    migrate()
    rows = [{**c, "start_ms": round(c["start_s"] * 1000), "end_ms": round(c["end_s"] * 1000)} for c in chapters]
    checkpoints = shuffle_options(checkpoints, slug)
    for cp in checkpoints:
        # Fail here, not in the API, if a stage's output drifted from the published shape
        cp["questions"] = [CheckpointQuestion.model_validate(q).model_dump() for q in cp["questions"]]
    with SessionLocal.begin() as db:
        return publish_video_lesson(
            db, slug, title, transcript, video["url"], video["duration_ms"], rows, checkpoints, voice, language, label
        ).version


def reset_stages(files: dict[str, Path], redo: str | None, share_from: str | None) -> None:
    """Delete the caches of `redo` and every later stage, then copy the shared transcript (in that order, so
    `--redo transcript` with `--share-from` copies it again instead of re-transcribing)."""
    if redo:
        for stage in STAGES[STAGES.index(redo) :]:
            files[stage].unlink(missing_ok=True)
    if share_from:
        src = settings.lessons_dir / share_from / "transcript.json"
        if not src.exists():
            raise SystemExit(f"--share-from: {src} not found; prepare {share_from} first")
        if not files["transcript"].exists():
            files["transcript"].write_text(src.read_text())


# The fixed lines the live video routes play (app.api.video_doubts, app.services.threads), as `speak_cached` keys
VIDEO_LINES = [*VIDEO_ACKS.values(), VIDEO_ACK_COVERED_NOW, VIDEO_ACK_NEUTRAL, VIDEO_FOLLOWUP_LINE, VIDEO_CAPPED_LINE, VIDEO_SORRY_LINE]


async def make_video_lines(titles: list[str], voice: str | None = None) -> int:
    """Pre-generate the lines spoken instantly around video doubts, in the lesson's voice: the acks, the thread
    lines and the template recap per chapter title (and the lesson title, used when a chapter is unknown).

    The live code speaks these English literals as they are (no `line_for`: video lines are not translated), so
    the cache keys match whatever `language` the lesson has. Returns how many lines were synthesized.
    """
    lines = [*VIDEO_LINES, *(VIDEO_RECAP_TEMPLATE.format(title=t) for t in titles)]
    made = 0
    for text in dict.fromkeys(lines):
        if not cached_path(text, voice).exists():
            made += 1
        await speak_cached(text, voice)
    log.info("video lines: %d cached, %d synthesized", len(lines) - made, made)
    return made


def published_lesson(slug: str) -> tuple[str, list[str], str | None]:
    """The published lesson's title, chapter titles and voice (its latest version)."""
    with SessionLocal() as db:
        lesson = latest_lesson(db, slug)
        if not lesson or lesson.kind != "video":
            raise SystemExit(f"no published video lesson '{slug}'")
        return lesson.title, [c.title for c in lesson.chapters], lesson.voice


async def run(args: argparse.Namespace) -> None:
    if args.lines_only:
        # Just the fixed lines, for a lesson that is already published (no stage is rerun, nothing is republished)
        title, titles, voice = published_lesson(args.slug)
        await make_video_lines([*titles, title], args.voice or voice)
        return
    if not args.video:
        raise SystemExit("the video file is required unless --lines-only is given")
    out = settings.lessons_dir / args.slug
    out.mkdir(parents=True, exist_ok=True)
    files = {s: out / f"{s}.json" for s in STAGES}
    reset_stages(files, args.redo, args.share_from)

    async def cached(stage: str, make):
        if files[stage].exists():
            log.info("%s: cached", stage)
            return json.loads(files[stage].read_text())
        log.info("%s: generating", stage)
        value = await make()
        files[stage].write_text(json.dumps(value, ensure_ascii=False, indent=1))
        return value

    video = await cached("video", lambda: asyncio.to_thread(encode_video, Path(args.video), args.slug))
    video_path = settings.media_dir / "videos" / f"{args.slug}.mp4"
    duration_s = video["duration_ms"] / 1000

    current_stt_language.set(args.lecture_language)
    # Transcribed from the encoded file, so the time marks are on the video's own timeline
    transcript = await cached("transcript", lambda: make_transcript(video_path, 0, duration_s / 60))

    outline = await cached("chapters", lambda: make_chapters(transcript, duration_s))
    chapters = outline["chapters"]
    checkpoints = await cached(
        "checkpoints", lambda: make_checkpoints(chapters, transcript, video_path, duration_s, args.voice, args.language)
    )
    if not checkpoints:
        log.warning("no checkpoint survived planning; the lesson will have none")

    # Remediation and narration use the reviewed questions
    reviews = await cached("review", lambda: review_checkpoints(checkpoints, chapters, transcript, args.voice, args.language))
    checkpoints = [
        {**cp, "questions": [CheckReview.model_validate(r).check_question.model_dump() for r in rs]}
        for cp, rs in zip(checkpoints, reviews)
    ]
    remedies = await cached("remedy", lambda: remediate(checkpoints, chapters, transcript, args.voice, args.language))
    spoken = await cached("audio", lambda: narrate(args.slug, checkpoints, remedies, args.voice))
    await make_fillers(args.voice, args.language)
    await make_video_lines([*(c["title"] for c in chapters), outline["title"]], args.voice)
    version = load_db(
        args.slug, outline["title"], transcript, video, chapters, spoken, args.voice, args.language, args.label
    )
    questions = sum(len(cp["questions"]) for cp in spoken)
    log.info(
        "published video lesson '%s' (%s v%d): %s, %d chapters, %d checkpoints, %d questions",
        outline["title"], args.slug, version, mmss(duration_s), len(chapters), len(spoken), questions,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", nargs="?", help="the lecture video (not needed with --lines-only)")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--redo", choices=STAGES)
    parser.add_argument(
        "--lines-only",
        action="store_true",
        help="only pre-generate the fixed lines (acks, thread lines, template recaps) for the published lesson",
    )
    parser.add_argument("--voice", help="voice name from data/voices/ (default: the configured stock voice)")
    parser.add_argument("--language", choices=["hinglish", "english"], default="english", help="what the tutor speaks")
    parser.add_argument(
        "--lecture-language", choices=["hindi", "english"], default="english", help="language of the recording (for STT)"
    )
    parser.add_argument("--share-from", help="reuse this slug's transcript")
    parser.add_argument("--label", help="short build note shown as a tag in the app, e.g. 'Checkpoints v2'")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
