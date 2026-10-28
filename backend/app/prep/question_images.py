"""Attach diagrams to a published video lesson's checkpoint questions, in question order.

    .venv/bin/python -m app.prep.question_images --slug lecture-05-video ~/Downloads/"image (0).png" … ~/Downloads/"image (9).png"

The images are given in the order the questions are asked (checkpoints by time, then their questions; see
docs/review/<slug>-questions.md), scaled down to at most 1000 px on the long side, saved as
media/lessons/<slug>/questions/qN.png and set as each question's `image` in the stored lesson (its latest version),
so checkpoint edits made in the database are kept. Pass fewer images than questions to leave the rest without one.
"""

import argparse
import json
import subprocess
from pathlib import Path

from app.config import settings
from app.db import Checkpoint, SessionLocal
from app.lesson_store import latest_lesson

LONG_SIDE = 1000


def save_image(src: Path, out: Path) -> tuple[int, int]:
    """Scale `src` to fit LONG_SIDE (never up) as a PNG at `out`; returns its pixel size."""
    out.parent.mkdir(parents=True, exist_ok=True)
    fit = f"scale='min({LONG_SIDE},iw)':'min({LONG_SIDE},ih)':force_original_aspect_ratio=decrease"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", fit, str(out)], check=True)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(out)],
        check=True, capture_output=True, text=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    return stream["width"], stream["height"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slug", required=True)
    parser.add_argument("images", nargs="+", type=Path, help="one image per question, in question order")
    args = parser.parse_args()

    with SessionLocal.begin() as db:
        lesson = latest_lesson(db, args.slug)
        if not lesson or lesson.kind != "video":
            raise SystemExit(f"no published video lesson '{args.slug}'")
        checkpoints = db.query(Checkpoint).filter_by(lesson_id=lesson.id).order_by(Checkpoint.at_ms).all()
        total = sum(len(cp.questions) for cp in checkpoints)
        if len(args.images) > total:
            raise SystemExit(f"{len(args.images)} images for {total} questions")
        images = iter(args.images)
        n = 0
        for cp in checkpoints:
            questions = []
            for q in cp.questions:
                src = next(images, None)
                if src is not None:
                    n += 1
                    rel = f"lessons/{args.slug}/questions/q{n}.png"
                    w, h = save_image(src, settings.media_dir / rel)
                    q = {**q, "image": {"url": f"/media/{rel}", "w": w, "h": h}}
                    print(f"Q{n} ({w}x{h}) <- {src.name}: {q['question'][:70]}")
                questions.append(q)
            cp.questions = questions


if __name__ == "__main__":
    main()
