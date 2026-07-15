# skills/ - where Agent Skills live

This folder holds [agentskills.io](https://agentskills.io) / Anthropic **Agent Skills** -
each a folder with a `SKILL.md` (YAML front-matter + instructions), optionally plus
`scripts/`, `references/`, and `assets/`. The same portable format Claude Code uses.

## document-insights (MVP-1, working)

The first real skill. It analyzes a text document and pairs:

- **Deterministic** ([`document-insights/scripts/analyze.py`](document-insights/scripts/analyze.py)):
  exact word / character / sentence counts, reading time - facts an LLM cannot count reliably.
- **Non-deterministic** (the LLM, guided by [`document-insights/SKILL.md`](document-insights/SKILL.md)):
  sentiment, a one-line summary, and key themes - judgment the LLM is good at.

The output labels which half is code and which is the LLM, so an LLM guess never masquerades
as a hard fact. This is the whole point of a skill, in the smallest honest form.

```text
document-insights/
  SKILL.md                 # what to do and when (instructions)
  scripts/analyze.py       # the deterministic half (pure Python, no LLM)
```

## How a job runs a skill

[`../src/run_skill.py`](../src/run_skill.py) is the skill runner. Given `--skill-dir`, it:

1. imports and runs the skill's `scripts/analyze.py` for exact metrics,
2. reads `SKILL.md` and calls the inside-Databricks LLM for the interpretive read,
3. writes a combined, labeled report to the output volume.

The DAB passes the deployed skill path via `--skill-dir ${workspace.file_path}/skills/document-insights`
(serverless runs the task with no `__file__`, so the path is passed in explicitly).

## Coming next: branded-pptx (MVP-2 / MVP-3)

branded-pptx turns a markdown document into a branded PowerPoint deck. Its real engine is
`pptxgenjs` (Node.js) + LibreOffice + Poppler with a multimodal vision-in-the-loop QA cycle -
**none of which run on Databricks Free Edition** (serverless-only: no Node, no system packages).
So:

- **MVP-2** re-cuts it in pure-Python `python-pptx` (runs on free serverless; lower fidelity, no vision loop).
- **MVP-3** runs it faithfully on a paid tier with classic compute (the self-correcting vision loop).

## Reuse note

Today the skill is bundled with the DAB and deploys alongside the job. To make a skill
**install-once, reuse-everywhere** across a Databricks workspace (like `~/.claude/skills/` in
Claude Code), publish it as a wheel (or a folder) on a shared Unity Catalog volume and depend on it
instead of bundling a copy per project - see
[`../docs/skill-reuse-on-databricks.md`](../docs/skill-reuse-on-databricks.md).
