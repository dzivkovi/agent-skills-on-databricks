---
name: branded-pptx
description: "Turn a markdown document into a branded PowerPoint (.pptx) deck: a title slide from the H1, one content slide per H2, bullets from list items, with a light brand skin (accent colour + footer). Pure-Python (python-pptx), runs on Databricks Free Edition serverless - no Node, no LibreOffice. Use when you need a real .pptx from markdown without a design toolchain. Triggers: 'make a deck', 'markdown to powerpoint', 'branded pptx', 'slides from this doc', 'pptx from markdown'."
metadata:
  author: Daniel Zivkovic
  version: 0.1.0
  fidelity: mvp-2
---

# Branded PPTX (MVP-2)

The MVP-2 rung of the branded-pptx ladder. It re-cuts the faithful branded-pptx skill in pure
Python so it runs on Free Edition serverless. The faithful engine (pptxgenjs on Node + LibreOffice
+ Poppler, with a multimodal vision-in-the-loop QA cycle) needs Node and system packages that
serverless does not have; MVP-4 restores it on paid classic compute. MVP-2 trades that fidelity
for portability: a deterministic markdown-to-slides map with a light brand skin, no vision QA.

## How to run this skill

1. **Deterministic step** - run `scripts/build_pptx.py <input.md> [-o out.pptx] [--brand coral]`.
   It parses the markdown structurally and writes a real `.pptx`:
   - the first `# H1` becomes the title slide (the first paragraph after it is the subtitle);
   - each `## H2` becomes a content slide titled with that heading;
   - `-` / `*` list items (and plain paragraphs) under a heading become the slide's bullets.
   The build is a pure function of the markdown - same input, same deck. No LLM.
2. **Optional non-deterministic step** - before building, an LLM may restructure or condense a
   long document into the H1/H2/bullets shape this skill expects (outline the deck). That step is
   advisory and out of scope for MVP-2; the deterministic builder is the contract.

## Output contract

A valid `.pptx` (openable by PowerPoint / Keynote / Google Slides / python-pptx) with:

- one title slide carrying the H1 (accent-coloured) and the subtitle;
- one content slide per H2, each titled (accent-coloured) with its bullets;
- a small brand footer when the chosen brand defines one.

Brands are a skin only (accent colour + footer label), defined in `BRANDS` in the build script -
add one there without touching the layout code. This is intentionally low fidelity: it is a
correct, serverless-safe deck, not a pixel-perfect brand system (that is MVP-4's job).

## Why this rung exists

An LLM cannot emit a binary `.pptx`, and the faithful toolchain cannot run on serverless. So the
deterministic Python builder is the only half that can produce the artifact here - and being
deterministic, it is fully testable (build a deck, reopen it, assert the slides) without a vision
loop. That honesty - shipping the rung that actually runs, and labelling its fidelity - is the
point.
