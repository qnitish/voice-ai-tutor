"""Small spoken samples from the real prompts, to hear a prompt or voice change before re-preparing a whole lesson.

    .venv/bin/python scripts/voice_samples.py --tag v1                    # current prompts
    .venv/bin/python scripts/voice_samples.py --tag v0 --no-pause-rules   # baseline without PAUSE_RULES

Takes one scene of an already prepared lesson (default: lecture-05-sarvam scene 0, free fall) and writes, in that
lesson's voice: the scene narration, its check question, the misconception hints and twin question (remedy), and
the answer to one doubt. Audio and a manifest go to data/media/samples/<name>/<tag>/; index.html there plays
every tag side by side (http://localhost:8010/media/samples/<name>/index.html).
"""

import argparse
import asyncio
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import llm, prompts  # noqa: E402
from app.audio import speak  # noqa: E402
from app.config import settings  # noqa: E402
from app.prep import __main__ as prep  # noqa: E402
from app.prompts import ANSWER_SYSTEM  # noqa: E402
from app.schemas import Outline  # noqa: E402
from app.voices import get_voice  # noqa: E402

DOUBT = "Agar ball ko seedha upar phenke, toh highest point par uski velocity aur acceleration kitne honge?"


def without_pause_rules(for_gender):
    def wrapped(system: str, gender: str, language: str = "hinglish") -> str:
        out = for_gender(system, gender, language)
        for rules in (prompts.SPEECH_RULES, prompts.SPEECH_RULES_EN):
            head = rules[: rules.index("- Pace the speech")]
            out = out.replace(rules, head.rstrip("\n"))
        return out

    return wrapped


async def answer(system: str, scene, meta, transcript: str) -> list[str]:
    user = (
        f"Student: Test, class unknown, weak concepts so far: none\n"
        f"Doubt intent: confused\nDoubt: {DOUBT}\n\n"
        f"Current scene 0: {meta.title}\nGoal: {meta.goal}\n"
        f"Narration so far: {' '.join(s.say for s in scene.steps)}\n\n"
        f"Lecture transcript:\n{transcript}"
    )
    lines = []
    async for line in llm.stream_lines(system, user):
        data = json.loads(line)
        if "say" in data:
            lines.append(data["say"])
    return lines


def write_index(root: Path) -> None:
    tags = sorted(p.name for p in root.iterdir() if (p / "manifest.json").exists())
    cols = []
    for tag in tags:
        m = json.loads((root / tag / "manifest.json").read_text())
        rows = "".join(
            f'<div class="clip"><b>{html.escape(c["kind"])}</b> <span>{c["seconds"]:.1f}s</span>'
            f'<p>{html.escape(c["text"])}</p><audio controls preload="none" src="{tag}/{c["file"]}"></audio></div>'
            for c in m["clips"]
        )
        cols.append(f'<section><h2>{tag}</h2><p class="note">{html.escape(m["note"])}</p>{rows}</section>')
    (root / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><title>Voice samples</title>"
        "<style>body{font:14px system-ui;margin:16px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}"
        ".clip{border-top:1px solid #ddd;padding:8px 0}.clip span,.note{color:#666}audio{width:100%}</style>"
        f"<h1>Voice samples: {html.escape(root.name)}</h1><main>{''.join(cols)}</main>"
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tag", required=True, help="version label, e.g. v1")
    parser.add_argument("--lesson", default="lecture-05-sarvam")
    parser.add_argument("--scene", type=int, default=0)
    parser.add_argument("--voice", default="lecture5-sarvam")
    parser.add_argument("--language", choices=["hinglish", "english"], default="hinglish")
    parser.add_argument("--name", default="free-fall", help="sample set; folder under media/samples/")
    parser.add_argument("--no-pause-rules", action="store_true", help="prompts without PAUSE_RULES (baseline)")
    parser.add_argument("--note", default="", help="what changed in this version")
    args = parser.parse_args()

    for_gender = prompts.for_gender
    if args.no_pause_rules:
        for_gender = without_pause_rules(for_gender)
        prep.for_gender = for_gender

    src = settings.lessons_dir / args.lesson
    outline = Outline.model_validate_json((src / "outline.json").read_text())
    transcript = json.loads((src / "transcript.json").read_text())
    meta = outline.scenes[args.scene]

    scene = await prep.make_scene(outline, args.scene, transcript, [], args.voice, args.language)
    remedy = await prep.make_remedy(meta, scene, args.voice, transcript, args.language)
    system = for_gender(ANSWER_SYSTEM, get_voice(args.voice).gender, args.language)
    answer_lines = await answer(system, scene, meta, transcript)

    q = scene.check_question
    texts = [(f"narration_{i + 1}", s.say) for i, s in enumerate(scene.steps)]
    texts.append(("check_question", q.say))
    texts += [(f"misconception_hint_option{h.option}", h.hint) for h in remedy.why_wrong]
    texts += [(f"reteach_{i + 1}", s.say) for i, s in enumerate(remedy.reteach)]
    texts.append(("twin_question", remedy.twin.say))
    texts += [(f"doubt_answer_{i + 1}", t) for i, t in enumerate(answer_lines)]

    root = settings.media_dir / "samples" / args.name
    rel = f"samples/{args.name}/{args.tag}"
    clips = await asyncio.gather(
        *(speak(text, f"{rel}/{i:02d}_{kind}.wav", args.voice) for i, (kind, text) in enumerate(texts))
    )
    note = args.note or ("prompts without PAUSE_RULES (baseline)" if args.no_pause_rules else "current prompts")
    manifest = {
        "tag": args.tag,
        "note": f"{note}; voice {args.voice}, {args.language}, {args.lesson} scene {args.scene} ({meta.title})",
        "doubt": DOUBT,
        "check_question": {"question": q.question, "options": q.options, "correct_index": q.correct_index},
        "clips": [
            {"kind": kind, "file": f"{i:02d}_{kind}.wav", "text": text, "seconds": c.duration_ms / 1000}
            for i, ((kind, text), c) in enumerate(zip(texts, clips))
        ],
    }
    (root / args.tag / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    write_index(root)
    print(f"{len(clips)} clips in {root / args.tag}")
    print(f"listen: http://localhost:8010/media/{rel.rsplit('/', 1)[0]}/index.html")


if __name__ == "__main__":
    asyncio.run(main())
