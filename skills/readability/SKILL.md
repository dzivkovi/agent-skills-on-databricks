---
name: readability
description: "Grade how readable a text document is by pairing EXACT, code-computed readability metrics (Flesch Reading Ease, Flesch-Kincaid grade level, syllable and hard-word counts) with an LLM's plain-language coaching (who can read this, and the two edits that would lower the grade most). Use when you need a grounded readability assessment where the scores must be correct, not estimated. Triggers: 'how readable is this', 'reading level', 'grade level', 'make this easier to read', 'plain language check'."
metadata:
  author: Daniel Zivkovic
  version: 0.1.0
  # Each skill can pick its own serving endpoint; the runner honors this unless --model is
  # passed explicitly. Free tier only calls gpt-oss, so this demo uses it; a paid skill could
  # set databricks-claude-opus-4-8 here and nothing else would change.
  model: databricks-gpt-oss-120b
---

# Readability

The second skill in this repo, and proof that the runner is skill-agnostic: same two-half
shape as document-insights (deterministic facts + LLM judgment), but a DIFFERENT contract.
Here the exact half computes readability scores; the LLM half coaches how to improve them.

## How to run this skill

1. **Deterministic step** - run `scripts/analyze.py <input-file>`. It returns a JSON object
   of exact metrics: `word_count`, `sentence_count`, `syllable_count`,
   `avg_syllables_per_word`, `flesch_reading_ease`, `flesch_kincaid_grade`,
   `hard_word_count`, `longest_sentence_words`. These scores are ground truth. Never let the
   LLM recompute or "estimate" a grade.
2. **Non-deterministic step** - ask the model for a plain-language read of the scores: name
   the likely audience (e.g. "general adult reader", "needs a college reading level"), and
   give the two concrete edits that would most lower the Flesch-Kincaid grade (shorten the
   longest sentences, swap the multi-syllable words). Ground every claim in the metrics and
   the document; never invent a score.

## Output contract

Produce markdown with two clearly-labeled sections so a reader can see the seam:

- **Scores (computed by code - exact)** - a table of the numbers from `analyze.py`, with a
  one-line reading of what the Flesch Reading Ease band means (90-100 very easy, 60-70 plain
  English, 30-50 difficult, 0-30 very difficult).
- **Coaching (interpreted by the LLM)** - the audience read plus the top two edits to lower
  the grade, each tied to a specific metric.

Use hyphens, not em-dashes. Keep it concise.

## Why this split matters

An LLM asked "what grade level is this?" will guess. `analyze.py` computes the Flesch
scores exactly from syllables and sentence length. The LLM cannot count syllables reliably,
but it is good at turning a grade into actionable coaching - so each half does what it is
good at, and the output labels which is which.
