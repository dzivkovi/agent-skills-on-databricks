"""
Deterministic document metrics for the document-insights skill.

Pure Python, no LLM. Prints a JSON object of EXACT metrics to stdout so any runner
(or a human) can consume it. LLMs are unreliable at exact counting; this is the
ground-truth half of the skill.

Usage:
    python scripts/analyze.py <input-file>     # or pipe text on stdin
"""
import json
import re
import sys


def analyze(text: str) -> dict:
    words = re.findall(r"\b\w[\w'-]*\b", text)
    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    word_count = len(words)
    sentence_count = len(sentences)
    return {
        "word_count": word_count,
        "char_count": len(text),
        "char_count_no_spaces": len(re.sub(r"\s", "", text)),
        "line_count": text.count("\n") + 1 if text else 0,
        "sentence_count": sentence_count,
        "avg_words_per_sentence": round(word_count / sentence_count, 1) if sentence_count else 0.0,
        "reading_time_min": round(word_count / 200, 2),  # ~200 words per minute
        "longest_word": max(words, key=len) if words else "",
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
