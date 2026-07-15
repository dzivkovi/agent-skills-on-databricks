# skills/ - where Agent Skills live

This folder holds [agentskills.io](https://agentskills.io) / Anthropic **Agent Skills** -
each a folder with a `SKILL.md` (YAML front-matter + instructions), optionally plus
`scripts/`, `references/`, and `assets/`. The same portable format Claude Code uses.

## Status: empty on purpose (for now)

MVP-0 (the current job in [`../src/convert_document.py`](../src/convert_document.py))
does **not** run a skill yet. It calls the inside-Databricks LLM with a fixed
instruction to prove the pipeline: `input volume -> LLM -> output volume`.

The first skill to migrate here is **branded-pptx** (turns a markdown document into a
branded PowerPoint deck). It is NOT copied in yet because of a hard platform fact:

## Why the skill is not just "dropped in and run"

branded-pptx's engine is `pptxgenjs` (Node.js) + LibreOffice + Poppler, driven by a
multimodal vision-in-the-loop QA cycle. **None of those run on Databricks Free Edition**,
which is serverless-only (no Node, no system packages, no classic clusters). So a
faithful "run the skill as-is" needs a PAID tier + classic compute (that is MVP-2).

The free-tier path (MVP-1) is a **re-cut**: re-implement branded-pptx's brand tokens and
layout helpers from its `SKILL.md` in pure-Python `python-pptx` (which DOES run on
serverless), producing a real `.pptx` without Node or LibreOffice. Lower fidelity (no
self-correcting vision loop), but it runs on this account today.

## Layout when a skill lands here

```
skills/
  branded-pptx/
    SKILL.md            # the portable skill definition
    scripts/            # deterministic helpers (re-cut for python-pptx on free tier)
    assets/             # brand fonts/logos, pre-staged (avoids runtime downloads)
    references/         # supporting docs the skill loads on demand
```

## How a job will load a skill (MVP-1)

The DAB bundles `skills/` into the workspace alongside `src/`. The job reads the target
`SKILL.md`, follows its instructions in code, and reads the input document from the
**input volume** (`/Volumes/workspace/genai/input/`), writing the deck to the **output
volume** (`/Volumes/workspace/genai/output/`).
