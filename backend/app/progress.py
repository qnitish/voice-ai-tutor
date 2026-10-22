"""Concept mastery: a 0..1 score per guest and concept, moved by check answers and doubts."""

PRIOR = 0.5
# How far one check answer moves the score toward 1 (correct) or 0 (wrong)
ALPHA_CHECK = 0.4
# A second try at the same question after a hint is weaker evidence than a first answer
ALPHA_RETRY = 0.2
# A confused / repeat / prerequisite doubt is weaker evidence than a wrong answer
ALPHA_DOUBT = 0.15
WEAK_BELOW = 0.5


def _clamp(x: float) -> float:
    return min(1.0, max(0.0, x))


def next_mastery(score: float | None, correct: bool) -> float:
    s = PRIOR if score is None else score
    return _clamp(s + ALPHA_CHECK * ((1.0 if correct else 0.0) - s))


def retry_mastery(score: float | None, correct: bool) -> float:
    s = PRIOR if score is None else score
    return _clamp(s + ALPHA_RETRY * ((1.0 if correct else 0.0) - s))


def doubt_mastery(score: float | None) -> float:
    s = PRIOR if score is None else score
    return _clamp(s - ALPHA_DOUBT * s)
