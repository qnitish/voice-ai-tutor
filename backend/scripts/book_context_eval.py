"""Answer video doubts (and follow-ups) with and without the lesson's textbook pages, and compare.

    .venv/bin/python scripts/book_context_eval.py --slug lecture-05-video --set followups

Each conversation is answered twice by the live answer LLM with the production prompt (VIDEO_ANSWER_SYSTEM and
video_answer_prompt): first without the book, then with it. A follow-up sees the earlier turns of its own run as the
thread history, as in the app (turn 1, answered on the same board). The intent of each turn is given here instead of
routed, so both runs answer the same thing. No speech is synthesized. Writes a JSON and a Markdown report to
data/evals/book-context/ with the answers side by side, the expected answer, the time to the first spoken line, the
total time and the token usage (cache reads show the book pages coming from the provider's cache).
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app import llm  # noqa: E402
from app.api import video_doubts  # noqa: E402
from app.api.video_doubts import chapter_at, history_text, mmss, video_answer_prompt  # noqa: E402
from app.config import settings  # noqa: E402
from app.context.book import book_parts  # noqa: E402
from app.db import Doubt, Lesson, SessionLocal  # noqa: E402
from app.prompts import VIDEO_ANSWER_SYSTEM  # noqa: E402
from app.providers import anthropic_llm  # noqa: E402

# Conversations: (paused at, [(intent, what the student says), ...], what a right answer contains)
SETS = {
    "basic": [
        (100_000, [("not_covered", "Does a heavier object fall faster than a lighter one?")], "only the book covers it"),
        (160_000, [("not_covered", "What is the value of g, and is it the same everywhere on Earth?")],
         "only the book covers it"),
        (410_000, [("confused", "At the highest point the velocity is zero, so is the acceleration also zero there?")],
         "lecture and book (the book's caution box)"),
        (580_000, [("confused", "Why do we reject the negative root of the time?")], "lecture and book (Example 2.8)"),
        (930_000, [("confused", "Why do we use t minus t naught for the second particle?")],
         "lecture only; the book may distract"),
    ],
    "followups": [
        (100_000, [
            ("not_covered", "If there is air resistance, is it still free fall, and does the heavy ball reach first then?"),
            ("not_covered", "So on the Moon, would a feather and a hammer land together?"),
        ], "With air it is not free fall (a ≠ g), the heavier/denser body usually lands first. Moon: no air, both fall "
           "with the same g (about 1.6 m/s², book p.52), so they land together."),
        (160_000, [
            ("not_covered", "If I drop a ball from a moving train, what is its initial velocity?"),
            ("not_covered", "And for someone standing on the platform, does the ball fall straight down?"),
        ], "Initial velocity = the train's velocity (lecture's misconception point). Platform observer: it moves forward "
           "with the train's speed while falling, a curved (projectile) path; motion in 2D comes later."),
        (330_000, [
            ("confused", "Why does the packet's acceleration become g immediately, and not a naught like the balloon?"),
            ("confused", "But for a tiny moment while it is leaving, isn't the balloon still pushing it?"),
        ], "Once released, the only force is mg (no contact with the balloon), so a = g; velocity is inherited, "
           "acceleration is not. Contact force acts only while touching; after release it is zero."),
        (440_000, [
            ("confused", "Why is the displacement minus h naught when the packet first goes up?"),
            ("confused", "If I take downward as positive instead, how does the equation change, and is the time the same?"),
        ], "Displacement is final minus initial position (ground is h₀ below release), independent of the path. With "
           "down positive: +h₀ = −v₀t + ½gt²; same quadratic, same physical time."),
        (520_000, [
            ("confused", "Can g ever be written as positive in the equation?"),
            ("not_covered", "In my textbook they write a y equals minus g. Is that the same as minus g j cap?"),
        ], "g itself is always a positive magnitude; the sign of a depends on the chosen positive direction (+g if down "
           "is positive). The book's a_y = −g with +y up is the same as a = −g ĵ."),
        (580_000, [
            ("confused", "When would both roots of the quadratic be acceptable?"),
            ("not_covered", "Can you give me an example with numbers where both roots are positive?"),
        ], "When asking when the particle is at a height above the release point (it passes it going up and coming "
           "down). Book Example 2.8: v₀ = 15 m/s up, y = +5 m gives t = 0.38 s and 2.68 s."),
        (660_000, [
            ("confused", "How do you get t one equals v naught by g for reaching the highest point?"),
            ("not_covered", "And what is the maximum height above the release point, in terms of v naught and g?"),
        ], "At the top v = 0: 0 = v₀ − g t₁, so t₁ = v₀/g. Max height h = v₀²/2g (from v² = v₀² − 2gh; book Example "
           "2.7: 11.5 m for 15 m/s)."),
        (760_000, [
            ("confused", "Why does the single vector equation give the total time without splitting it into up and down?"),
            ("confused", "Does that mean the equation doesn't care that the velocity changes direction at the top?"),
        ], "The acceleration is the same constant g throughout, so one constant-acceleration equation covers the whole "
           "motion; the sign of v changes by itself (v = v₀ − gt). Splitting is needed only if a changes."),
        (860_000, [
            ("confused", "Why do both particles have the same displacement when they meet?"),
            ("not_covered", "What if the second particle is thrown upward instead of downward? Can it still catch the first?"),
        ], "Same start point, same meeting point, so the same displacement. Thrown upward later, it starts behind and "
           "slower; both have acceleration g, so the gap only grows: it never catches the first."),
        (950_000, [
            ("confused", "How do I actually solve for t after equating the two equations?"),
            ("not_covered", "What happens if v naught is less than g times t naught? Will they ever meet?"),
        ], "−½gt² = −v₀(t−t₀) − ½g(t−t₀)²: the t² terms cancel, a linear equation gives t = t₀(v₀ − ½gt₀)/(v₀ − gt₀). "
           "They meet (t > t₀) only if v₀ > g t₀: when v₀ < g t₀ the first particle is already faster, so they never meet."),
    ],
}


async def run(system: str, prompt: str, prefix: list[dict] | None, usage: list[dict]) -> dict:
    """One answer stream: its lines, time to the first spoken line, total time and token usage."""
    usage.clear()
    started = time.perf_counter()
    first_say = None
    source, says = "", []
    async for line in llm.stream_lines(system, prompt, **({"prefix": prefix} if prefix else {})):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "source" in row:
            source = row["source"]
        elif "say" in row:
            first_say = first_say or time.perf_counter() - started
            says.append(row["say"])
    total = time.perf_counter() - started
    return {
        "source": source,
        "say": says,
        "first_line_s": round(first_say or total, 2),
        "total_s": round(total, 2),
        "usage": usage[-1] if usage else {},
    }


async def conversation(db, sess, at_ms: int, turns: list[tuple[str, str]], prefix, usage) -> list[dict]:
    """Answer the turns in order; each follow-up sees the earlier turns as the thread history."""
    chapter = chapter_at(sess.lesson, at_ms)
    earlier: list[Doubt] = []
    video_doubts.thread_history = lambda _db, _thread_id, before=None: history_text(earlier)
    out = []
    for turn, (intent, question) in enumerate(turns):
        doubt = Doubt(session_id="eval", scene_idx=chapter, step_idx=0, question=question, said=question,
                      intent=intent, at_ms=at_ms, thread_id="eval" if turn else None, turn=turn)
        prompt = await video_answer_prompt(db, doubt, sess)
        result = await run(VIDEO_ANSWER_SYSTEM, prompt, prefix, usage)
        doubt.answer = [{"say": s} for s in result["say"]]
        earlier.append(doubt)
        out.append({"intent": intent, "question": question, **result})
    return out


def avg(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 2) if xs else 0.0


def report(slug: str, name: str, results: list[dict], pages: int) -> str:
    out = [f"# Book context eval: {slug}, set `{name}`", "",
           f"{datetime.now():%Y-%m-%d %H:%M}, model `{settings.azure_openai_deployment}`, {pages} book pages as images.", ""]
    for label, idx in (("First doubt", 0), ("Follow-up", 1)):
        rows = [r for r in results if len(r["without"]) > idx]
        if rows:
            out.append(f"- {label}: first line {avg([r['without'][idx]['first_line_s'] for r in rows])} s without / "
                       f"{avg([r['with'][idx]['first_line_s'] for r in rows])} s with the book; total "
                       f"{avg([r['without'][idx]['total_s'] for r in rows])} / {avg([r['with'][idx]['total_s'] for r in rows])} s")
    out += ["", "| # | Turn | Question | First line (s) without / with | Total (s) without / with | Input tokens without / with (cache read) |",
            "|---|---|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        for t, (a, b) in enumerate(zip(r["without"], r["with"])):
            ua, ub = a["usage"], b["usage"]
            ta = ua.get("input_tokens", 0) + ua.get("cache_read", 0) + ua.get("cache_write", 0)
            tb = ub.get("input_tokens", 0) + ub.get("cache_read", 0) + ub.get("cache_write", 0)
            out.append(f"| {i} | {t} | {a['question']} | {a['first_line_s']} / {b['first_line_s']} | {a['total_s']} / "
                       f"{b['total_s']} | {ta} / {tb} ({ub.get('cache_read', 0)}) |")
    for i, r in enumerate(results, 1):
        out += ["", f"## {i}. Paused at {r['at']} (chapter {r['chapter']})", f"Expected: {r['expect']}"]
        for mode in ("without", "with"):
            out += ["", f"### {mode.capitalize()} the book"]
            for t, a in enumerate(r[mode]):
                out += ["", f"**Turn {t} ({a['intent']}): {a['question']}**", f"- source: {a['source']}"]
                out += [f"- {s}" for s in a["say"]]
    return "\n".join(out) + "\n"


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--slug", default="lecture-05-video")
    ap.add_argument("--set", default="basic", choices=list(SETS))
    args = ap.parse_args()

    usage: list[dict] = []
    log = anthropic_llm.log_event

    def capture(event: str, **fields):
        if event == "llm.usage":
            usage.append(fields)
        log(event, **fields)

    anthropic_llm.log_event = capture
    prefix = book_parts(args.slug)
    if not prefix:
        sys.exit(f"No book pages for {args.slug}: run python -m app.prep.book first")

    results = []
    with SessionLocal() as db:
        lesson = db.scalars(select(Lesson).filter_by(slug=args.slug).order_by(Lesson.version.desc())).first()
        sess = SimpleNamespace(lesson=lesson, guest=SimpleNamespace(name="Asha", class_level=11, weak_concepts=[]))
        for at_ms, turns, expect in SETS[args.set]:
            print(f"{mmss(at_ms)} {turns[0][1]}")
            results.append({
                "at": mmss(at_ms),
                "chapter": chapter_at(lesson, at_ms),
                "expect": expect,
                "without": await conversation(db, sess, at_ms, turns, None, usage),
                "with": await conversation(db, sess, at_ms, turns, prefix, usage),
            })

    out_dir = settings.data_dir / "evals" / "book-context"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}-{args.set}"
    (out_dir / f"{stamp}.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    (out_dir / f"{stamp}.md").write_text(report(args.slug, args.set, results, sum(p["type"] == "image" for p in prefix)))
    print(f"Report: {out_dir / f'{stamp}.md'}")


if __name__ == "__main__":
    asyncio.run(main())
