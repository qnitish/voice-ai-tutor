"""Small deterministic helpers for doubt routing."""

import re

AFFIRMATIVE = {
    "haan", "haa", "ha", "han", "haanji", "hanji", "ji",
    "yes", "yeah", "yep", "sahi", "bilkul",
    "हाँ", "हां", "हा", "जी",
}  # fmt: skip
_PUNCT = re.compile(r"""[.,!?।"'“”‘’]""")


def is_affirmative(text: str) -> bool:
    """True for a short "yes" reply ("haan", "haan ji bilkul", "Yes!") to a clarifying question."""
    words = _PUNCT.sub(" ", text.lower()).split()
    return 1 <= len(words) <= 4 and words[0] in AFFIRMATIVE
