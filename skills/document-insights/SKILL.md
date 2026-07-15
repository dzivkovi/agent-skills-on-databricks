---
name: document-insights
description: "Analyze a text document and produce an insights report that pairs EXACT, code-computed metrics (word/character/sentence counts, reading time) with an LLM's interpretive read (sentiment, one-line summary, key themes). Use when you need a grounded document summary where the numbers must be correct, not estimated. Triggers: 'analyze this document', 'document insights', 'sentiment and word count', 'summarize with stats'."
metadata:
  author: Daniel Zivkovic
  version: 0.1.0
---

# Document Insights

This skill demonstrates the core idea of an Agent Skill: pair DETERMINISTIC code (exact
facts an LLM cannot reliably compute) with NON-DETERMINISTIC LLM reasoning (judgment an
LLM is good at), and keep the two clearly separated in the output.

## How to run this skill

1. **Deterministic step** - run `scripts/analyze.py <input-file>`. It returns a JSON object
   of exact metrics: `word_count`, `char_count`, `char_count_no_spaces`, `line_count`,
   `sentence_count`, `avg_words_per_sentence`, `reading_time_min`, `longest_word`. These
   numbers are ground truth. Never let the LLM recompute or "estimate" them.
2. **Non-deterministic step** - ask the model for an interpretive read of the document:
   overall sentiment (positive / neutral / negative) with a one-phrase justification, a
   single-sentence summary, and 2-3 key themes. The model must ground its answer in the
   document text and may reference the exact metrics from step 1 (e.g. "a short 42-word
   note"), but must never invent or restate counts as if it computed them.

## Output contract

Produce markdown with two clearly-labeled sections so a reader can see the seam:

- **Metrics (computed by code - exact)** - a table of the numbers from `analyze.py`.
- **Reading (interpreted by the LLM)** - sentiment, one-sentence summary, key themes.

Use hyphens, not em-dashes. Keep it concise.

## Why this split matters

An LLM asked "how many words is this?" will guess, and often guess wrong. `analyze.py`
counts them exactly. Conversely, code cannot judge tone; the LLM can. The skill gets the
best of both by letting each do what it is good at - and by labeling which is which, the
output never passes an LLM guess off as a hard fact.
