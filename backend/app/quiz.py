"""Check-question option order. The LLM tends to put the right answer first, so we shuffle on publish."""

import math
import random


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
        correct = q["options"][q["correct_index"]]
        others = [o for j, o in enumerate(q["options"]) if j != q["correct_index"]]
        rng.shuffle(others)
        pos = target_of[i] if i in target_of else rng.randrange(len(q["options"]))
        options = others[:pos] + [correct] + others[pos:]
        out.append({**q, "options": options, "correct_index": pos})
    return out
