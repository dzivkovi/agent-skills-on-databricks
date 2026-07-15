"""
Deterministic readability metrics for the readability skill.

Pure Python, no LLM, stdlib only - the same "exact facts an LLM cannot reliably compute"
half that document-insights uses, but a DIFFERENT contract (readability grades, not word
counts). Two skills, two deterministic halves, one runner: that is #6's point.

Usage:
    python scripts/analyze.py <input-file>     # or pipe text on stdin
"""
import json
import re
import sys

_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
_VOWEL_GROUP = re.compile(r"[aeiouy]+")


def _syllables(word: str) -> int:
    """Heuristic syllable count: vowel groups, minus a silent trailing 'e', floored at 1.

    Not linguistically perfect, but deterministic and stable - which is the contract. The
    LLM half never recomputes it.
    """
    w = word.lower()
    groups = _VOWEL_GROUP.findall(w)
    count = len(groups)
    if w.endswith("e") and not w.endswith("le") and count > 1:
        count -= 1
    return max(1, count)


def analyze(text: str) -> dict:
    words = _WORD.findall(text)
    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    word_count = len(words)
    sentence_count = len(sentences)
    syllable_count = sum(_syllables(w) for w in words)
    hard_words = [w for w in words if _syllables(w) >= 3]

    words_per_sentence = word_count / sentence_count if sentence_count else 0.0
    syllables_per_word = syllable_count / word_count if word_count else 0.0

    # Flesch Reading Ease (higher = easier) and Flesch-Kincaid Grade (US grade level).
    flesch_reading_ease = (
        round(206.835 - 1.015 * words_per_sentence - 84.6 * syllables_per_word, 1)
        if word_count and sentence_count else 0.0
    )
    flesch_kincaid_grade = (
        round(0.39 * words_per_sentence + 11.8 * syllables_per_word - 15.59, 1)
        if word_count and sentence_count else 0.0
    )
    longest_sentence_words = (
        max(len(_WORD.findall(s)) for s in sentences) if sentences else 0
    )

    return {
        "word_count": word_count,
        "sentence_count": sentence_count,
        "syllable_count": syllable_count,
        "avg_syllables_per_word": round(syllables_per_word, 2),
        "flesch_reading_ease": flesch_reading_ease,
        "flesch_kincaid_grade": flesch_kincaid_grade,
        "hard_word_count": len(hard_words),
        "longest_sentence_words": longest_sentence_words,
    }


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            text = f.read()
    else:
        text = sys.stdin.read()
    print(json.dumps(analyze(text), indent=2))


if __name__ == "__main__":
    main()
