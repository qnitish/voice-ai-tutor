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


# Replies to "Did you get it?" that mean "move on", matched as whole words anywhere in a short reply
UNDERSTOOD = (
    "got it", "get it", "understood", "understand", "clear", "makes sense", "make sense", "samajh", "samjh",
    "samajh gaya", "samajh gayi", "samajh aa gaya", "continue", "next", "move on", "go on", "carry on", "aage",
    "chalo", "theek hai", "thik hai", "ok", "okay", "done", "fine", "perfect", "great", "cool", "acha", "accha",
    "achha", "समझ", "आगे",
)  # fmt: skip
# Any of these turns the reply into a follow-up ("samajh nahi aaya", "ok but why", "got it, what about…").
# _PUNCT splits "didn't" into "didn t", hence the bare stems.
NOT_UNDERSTOOD = {
    "not", "no", "nope", "nahi", "nahin", "nai", "na", "didn", "didnt", "don", "dont", "doesn", "doesnt", "isn",
    "wasn", "never",
    "but", "lekin", "par", "magar", "why", "what", "how", "when", "where", "which", "who", "kyun", "kyu", "kyon",
    "kya", "kaise", "kab", "kahan", "kaun", "matlab", "again", "phir", "dobara", "confused", "doubt", "question",
    "नहीं", "क्यों", "क्या", "कैसे",
}  # fmt: skip
UNDERSTOOD_MAX_WORDS = 6


def is_understood(text: str) -> bool:
    """True for a short reply to "Did you get it?" that means move on ("Yeah, I got it", "continue", "samajh gaya").

    A reply with a negation or a question word is a follow-up, not a yes; so is anything longer than a few words.
    """
    words = _PUNCT.sub(" ", text.lower()).split()
    if not 1 <= len(words) <= UNDERSTOOD_MAX_WORDS or NOT_UNDERSTOOD.intersection(words):
        return False
    if is_affirmative(text):
        return True
    padded = f" {' '.join(words)} "
    return any(f" {phrase} " in padded for phrase in UNDERSTOOD)
