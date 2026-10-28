"""Step-by-step solutions and spoken explanations for a published video lesson's checkpoint questions.

    .venv/bin/python -m app.prep.solutions --slug lecture-05-video [--redo]

For each question: the LLM writes the worked solution as narrated whiteboard steps (SOLUTION_SYSTEM), a second
call checks it against the answer key (SOLUTION_CHECK_SYSTEM; a solution that ends on another option or has a wrong
step is written again, at most three times, else the question gets none), and the steps and the explanation's first
sentences are voiced. The player shows a solution only when the student asks for it.

Works on the published lesson in the database (its latest version), so checkpoint edits made there are kept; the
results are also cached in data/lessons/<slug>/solutions.json by question text, so a rerun only does new questions.
"""

import argparse
import asyncio
import json
import logging

from pydantic import BaseModel, Field

from app import llm
from app.audio import speak
from app.config import settings
from app.db import Checkpoint, SessionLocal
from app.lesson_store import latest_lesson
from app.prep.video import transcript_between
from app.prompts import SOLUTION_CHECK_SYSTEM, SOLUTION_SYSTEM
from app.quiz import spoken_explanation
from app.schemas import StepDraft, clean_draw

log = logging.getLogger("prep.solutions")

LETTERS = "ABCD"
ATTEMPTS = 3
# The lecture before a checkpoint that its questions come from, and a little after (the pause snaps to a silence)
SECTION_AFTER_S = 30
PARALLEL = 3


class SolutionDraft(BaseModel):
    final_option: str
    steps: list[StepDraft] = Field(min_length=2, max_length=7)


class SolutionCheck(BaseModel):
    option: str
    correct: bool
    problems: str = ""


def letter(text: str) -> str:
    return text.strip().upper()[:1]


def question_text(q: dict) -> str:
    options = "".join(f"{LETTERS[i]}. {o}\n" for i, o in enumerate(q["options"]))
    return f"Question: {q['question']}\nOptions:\n{options}"


async def write_solution(q: dict, lecture: str) -> list[StepDraft] | None:
    """A solution the checker agrees with, or None after ATTEMPTS tries."""
    key = LETTERS[q["correct_index"]]
    user = (
        f"{question_text(q)}Correct option: {key}. {q['options'][q['correct_index']]}\n"
        f"Explanation: {q['explanation']}\n\nLecture transcript:\n{lecture}"
    )
    rejected = ""
    for attempt in range(ATTEMPTS):
        draft = await llm.prep_json(SOLUTION_SYSTEM, user + rejected, SolutionDraft)
        if letter(draft.final_option) != key:
            rejected = f"\n\nAn earlier solution ended on option {draft.final_option}, not {key}. Solve it again."
            continue
        steps = "\n".join(f"- says: {s.say}\n  writes: {[a.get('text') for a in s.draw if a.get('text')]}" for s in draft.steps)
        check = await llm.prep_json(SOLUTION_CHECK_SYSTEM, f"{question_text(q)}\nSolution steps:\n{steps}", SolutionCheck)
        if letter(check.option) == key and check.correct:
            return draft.steps
        rejected = f"\n\nAn earlier solution was rejected by the checker: {check.problems or f'it concluded {check.option}'}."
        log.info("solution rejected (attempt %d): %s", attempt + 1, rejected.strip())
    return None


async def prepare(slug: str, name: str, q: dict, lecture: str, voice: str | None) -> dict:
    """{"solution": [...clips], "explanation_audio": {...}} for one question (solution [] if none passed)."""
    steps = await write_solution(q, lecture)
    if steps is None:
        log.warning("%s: no solution passed the check; the question gets none", name)
    clips = []
    for n, step in enumerate(steps or []):
        spoken = await speak(step.say, f"lessons/{slug}/{name}_sol{n}.wav", voice)
        clips.append({"say": step.say, "draw": clean_draw(step.draw), "audio_url": spoken.audio_url, "duration_ms": spoken.duration_ms})
    explanation = await speak(spoken_explanation(q["explanation"]), f"lessons/{slug}/{name}_expl.wav", voice)
    return {"solution": clips, "explanation_audio": explanation.model_dump()}


async def run(slug: str, redo: bool) -> None:
    with SessionLocal() as db:
        lesson = latest_lesson(db, slug)
        if not lesson or lesson.kind != "video":
            raise SystemExit(f"no published video lesson '{slug}'")
        lesson_id, voice, transcript = lesson.id, lesson.voice, lesson.transcript
        checkpoints = [(c.idx, c.at_ms, [dict(q) for q in c.questions]) for c in sorted(lesson.checkpoints, key=lambda c: c.at_ms)]

    cache_path = settings.lessons_dir / slug / "solutions.json"
    cache: dict = {} if redo or not cache_path.exists() else json.loads(cache_path.read_text())
    gate = asyncio.Semaphore(PARALLEL)

    async def one(cp_idx: int, qi: int, q: dict, lecture: str) -> None:
        if q["question"] in cache:
            return
        async with gate:
            cache[q["question"]] = await prepare(slug, f"cp{cp_idx}_q{qi}", q, lecture, voice)
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
            log.info("cp%d q%d: %d steps", cp_idx, qi, len(cache[q["question"]]["solution"]))

    jobs = []
    start_s = 0.0
    for cp_idx, at_ms, questions in checkpoints:
        lecture = transcript_between(transcript, start_s, at_ms / 1000 + SECTION_AFTER_S)
        jobs += [one(cp_idx, qi, q, lecture) for qi, q in enumerate(questions)]
        start_s = at_ms / 1000
    await asyncio.gather(*jobs)

    # One short transaction at the end: no row stays locked while the LLM or TTS runs
    with SessionLocal.begin() as db:
        for cp in db.query(Checkpoint).filter_by(lesson_id=lesson_id):
            cp.questions = [{**q, **cache.get(q["question"], {})} for q in cp.questions]
    done = sum(bool(v["solution"]) for v in cache.values())
    log.info("%s: solutions for %d of %d questions saved", slug, done, len(cache))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slug", required=True)
    parser.add_argument("--redo", action="store_true", help="write every solution again, ignoring solutions.json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(args.slug, args.redo))


if __name__ == "__main__":
    main()
