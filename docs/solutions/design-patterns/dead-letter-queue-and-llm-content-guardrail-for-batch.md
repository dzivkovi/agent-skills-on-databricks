---
title: Dead-letter queue + LLM content guardrail for a Databricks batch skill
date: 2026-07-15
category: design-patterns
module: skill-runner (run_skill.py)
problem_type: design_pattern
component: background_job
severity: medium
applies_when:
  - "A batch/scheduled job processes untrusted documents"
  - "You must reject bad or unsafe inputs without failing the whole run"
  - "You want PII/unsafe screening without building a classifier from scratch"
symptoms:
  - "One malformed or malicious input fails the entire batch job"
  - "No place to inspect which inputs were rejected and why"
tags: [databricks, dead-letter-queue, guardrails, pii, llm-as-guardrail, batch, unity-catalog-volume]
related_components: [documentation]
---

# Dead-letter queue + LLM content guardrail for a Databricks batch skill

## Context

A batch job that runs an LLM skill over user documents will eventually get bad input: empty or
oversized files, or malicious/sensitive content (PII, unsafe text). Failing the whole run on one bad
record is wrong, and you rarely want to hand-build a PII or toxicity classifier. You need a reject
path and a screening step that are cheap and inspectable.

## Guidance

**Two guard layers, both feeding one dead-letter queue (a `rejected` Unity Catalog volume):**

1. **Structural guard (deterministic)** - reject empty/oversized inputs *before* any model call.
2. **Content guard (LLM-as-guardrail)** - ask the inside-Databricks model to classify the input for
   PII / unsafe content, and quarantine it if flagged.

Both write the offending input plus a `<name>.reason.txt` sidecar to the `rejected` volume and then
**return cleanly - the batch still SUCCEEDS.** One bad record never fails the run (the SQL\*Loader
reject-file / AWS dead-letter-queue pattern).

Design points that matter:

- The reject queue is a **third UC volume** beside `input`/`output`; an operator reviews it.
- **Fail open** on an unparseable guard verdict, so a guard hiccup never blocks legitimate content
  (production may prefer fail-closed - a deliberate choice).
- The platform-native alternative to the LLM classifier is **AI Gateway guardrails** (config, and
  supported on the pay-per-token Foundation Model API endpoints); the LLM-classifier is the portable,
  free-tier path.
- **Test guardrails safely in a public repo with obviously-fake PII** (`123-45-6789`,
  `4111 1111 1111 1111`, `@example.com`) - never real or offensive content. PII is the enterprise
  guardrail and tests cleanly.

## Why This Matters

Enterprise reviewers expect a reject/quarantine path, not a crash on bad data - and PII screening is
the number-one governance ask. Doing it as one LLM call (or a config checkbox) plus a volume reject
queue is cheap, inspectable, and governed. The batch-succeeds semantics keep a single poison record
from taking down a scheduled run, and the reason sidecar turns "it failed" into "here is exactly what
was rejected and why."

## When to Apply

- A batch or scheduled job over untrusted documents.
- You need to reject bad/unsafe inputs while the run still succeeds.
- Free tier (use the LLM classifier) or paid (native AI Gateway guardrails).

## Examples

**Both guards write to the reject queue, then the run returns cleanly** (`src/run_skill.py`, abridged):

```python
# structural guard
if not source.strip() or len(source) > MAX_INPUT_CHARS:
    write_rejected(rejected_dir, in_path, source, reason)   # + <name>.reason.txt
    return                                                   # batch still SUCCEEDS

# content guard (LLM-as-guardrail), fails OPEN on unparseable verdict
flagged, reason = guard_content(w, model, source)
if flagged:
    write_rejected(rejected_dir, in_path, source, reason)
    return
```

**The classifier prompt is a single call** returning strict JSON (`{"pii": bool, "unsafe": bool, ...}`);
parse it defensively and treat an unparseable answer as "not flagged" (fail open).

**Verified** by `scripts/e2e_reject_test.py`: a whitespace-only input and a fake-PII document both land
in the `rejected` volume with a reason, the job returns `SUCCESS`, and no output is produced - while a
benign document still passes (no false reject).

## Related

- [../../guardrails-and-dead-letter-queue.md](../../guardrails-and-dead-letter-queue.md) - full write-up with official source links and the "see it yourself" walkthrough.
- `scripts/e2e_reject_test.py` - the negative end-to-end test that proves the quarantine behavior.
