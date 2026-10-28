"""Check-question option order. The LLM tends to put the right answer first, so we shuffle on publish."""

import math
import random
import re


def shuffle_checks(checks: list[dict | None], seed: str) -> list[dict | None]:
    """Shuffle each question's options deterministically, spreading the correct answer over A-D.

    Four-option questions get their correct answer placed on a balanced, shuffled set of positions,
    so no position is correct more than ceil(n / 4) times. Others just get a random order.
    """
    rng = random.Random(seed)
    four = [i for i, q in enumerate(checks) if q is not None and len(q["options"]) == 4]
    targets = ([0, 1, 2, 3] * math.ceil(len(four) / 4))[: len(four)]
    rng.shuffle(targets)
    target_of = dict(zip(four, targets))

    out: list[dict | None] = []
    for i, q in enumerate(checks):
        if q is None:
            out.append(None)
            continue
        n, ci = len(q["options"]), q["correct_index"]
        others = [j for j in range(n) if j != ci]
        rng.shuffle(others)
        pos = target_of[i] if i in target_of else rng.randrange(n)
        order = others[:pos] + [ci] + others[pos:]
        moved = {**q, "options": [q["options"][j] for j in order], "correct_index": pos}
        # Each wrong-answer hint stays with its option
        if len(q.get("why_wrong") or []) == n:
            moved["why_wrong"] = [q["why_wrong"][j] for j in order]
        out.append(moved)
    return out


SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def spoken_explanation(text: str, sentences: int = 2) -> str:
    """The first sentences of a checkpoint explanation, spoken once the answer is shown (the board shows it all)."""
    return " ".join(SENTENCE_END.split(text.strip())[:sentences])
