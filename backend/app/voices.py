"""Tutor voices: the configured stock voice ("default"), or cloned ones saved in data/voices/<name>.json.

    python -m app.voices clone <audio-file> --name teacher-clone --gender male [--start 30] [--seconds 25]

Cloning only with the speaker's permission. A lesson stores its voice name (null = default); every line spoken
for that lesson (narration, acks, answers, feedback) uses it, and its prompts use matching first-person verbs.
"""

import argparse
import asyncio
import json
import re
from contextvars import ContextVar
from functools import cache
from typing import Literal

from pydantic import BaseModel

from app.config import settings

DEFAULT = "default"
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")


class Voice(BaseModel):
    name: str
    gender: Literal["female", "male"]
    # Gnani stock voice name, or the speaker_embedding from voice cloning
    stock: str | None = None
    embedding: dict | None = None


# The voice the TTS provider should use for the current synthesize() call; set by audio.speak
current_voice: ContextVar[Voice | None] = ContextVar("current_voice", default=None)


def voice_path(name: str):
    if not NAME.match(name):
        raise ValueError(f"bad voice name {name!r}")
    return settings.data_dir / "voices" / f"{name}.json"


@cache
def _load(name: str) -> Voice:
    return Voice.model_validate_json(voice_path(name).read_text())


def get_voice(name: str | None) -> Voice:
    if not name or name == DEFAULT:
        return Voice(name=DEFAULT, gender=settings.tutor_gender, stock=settings.gnani_tts_voice)
    return _load(name)


def is_default(name: str | None) -> bool:
    return get_voice(name).name == DEFAULT


def fillers_dir(name: str | None):
    """Fillers and bridges per voice; the default voice's live directly in media/fillers/."""
    return settings.media_dir / "fillers" / ("" if is_default(name) else name)


def exists(name: str | None) -> bool:
    try:
        get_voice(name)
        return True
    except (ValueError, OSError):
        return False


async def clone(audio_path: str, name: str, gender: str, start_s: float, seconds: float) -> Voice:
    from app.audio import STT_RATE, _decode_pcm, _pcm_to_wav
    from app.providers.gnani import embed_voice

    pcm = _decode_pcm(["-ss", str(start_s), "-t", str(seconds), "-i", audio_path], None, STT_RATE)
    sample = _pcm_to_wav(pcm, STT_RATE)
    voice = Voice(name=name, gender=gender, embedding=await embed_voice(sample))
    path = voice_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(voice.model_dump(), indent=1))
    # Keep the reference clip next to it, to check what was cloned
    path.with_suffix(".wav").write_bytes(sample)
    return voice


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("clone", help="clone a voice from 5-30 s of clean speech")
    c.add_argument("audio")
    c.add_argument("--name", required=True)
    c.add_argument("--gender", choices=["female", "male"], required=True)
    c.add_argument("--start", type=float, default=0, help="seconds into the file")
    c.add_argument("--seconds", type=float, default=25)
    args = parser.parse_args()
    if not 5 <= args.seconds <= 30:
        parser.error("the reference clip must be 5-30 seconds")
    voice = asyncio.run(clone(args.audio, args.name, args.gender, args.start, args.seconds))
    print(f"saved {voice_path(voice.name)} (shape {voice.embedding['shape']})")


if __name__ == "__main__":
    main()
