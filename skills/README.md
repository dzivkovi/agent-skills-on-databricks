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

## readability (MVP-1b, working - the second independent skill)

The second real skill, added to prove the runner is skill-agnostic (issue #6). Same two-half
shape, a DIFFERENT contract: the deterministic half
([`readability/scripts/analyze.py`](readability/scripts/analyze.py)) computes exact readability
scores (Flesch Reading Ease, Flesch-Kincaid grade, syllable and hard-word counts); the LLM half
turns those scores into plain-language coaching (audience + the two edits that most lower the
grade). Pure stdlib, no extra runtime dependency.

Both skills are published INDEPENDENTLY to the shared volume and consumed INDEPENDENTLY by the
job (`--skill-dir` selects one); updating one never touches the other. `tests/` proves this
without a workspace, and the live deployed job was verified consuming each skill from the volume
(`scripts/e2e_multiskill_test.py`). Per-skill model *selection* (below) is unit-verified;
skill *selection* is both unit- and live-verified.

**Each skill can pick its own model.** A skill may declare `model:` in its `SKILL.md`
front-matter (readability does); the runner honors it unless an explicit `--model` is passed.
Precedence: explicit `--model` (CLI/job) -> the skill's declared `model:` -> the built-in
default. So a cheap skill stays on free-tier `gpt-oss` while a paid skill could ask for
`databricks-claude-opus-4-8`, all by configuration - no code change.

## How a job runs a skill

[`../src/run_skill.py`](../src/run_skill.py) is the skill runner, and it owns only the
plumbing common to every skill: reading the input, the structural and content guards (bad
or blocked input goes to the reject queue instead of failing the batch), resolving the
model from `SKILL.md`, a retrying inside-Databricks LLM client, and the output naming. It
never hardcodes a report shape. Instead each skill ships its own `scripts/run.py` exposing
`run(ctx) -> output_path`, and the skill owns its behavior and output shape from there -
document-insights and readability write a two-section markdown report (metrics table +
LLM reading); branded-pptx writes a real `.pptx` with no LLM call at all.

The runner builds and passes a frozen `ctx` dict:

| key | what it is |
| --- | --- |
| `text` | the full input document text (guards already passed) |
| `in_path` | source file path (use only the basename for provenance lines) |
| `out_dir` | destination directory, already created |
| `out_base` | `<out_dir>/<stem>-<skill>-<date>` with no extension - append `.md` or `.pptx` |
| `skill_dir` | path to the skill folder on this filesystem |
| `skill_md` | full `SKILL.md` text |
| `skill_name` | skill folder basename, e.g. `document-insights` |
| `model` | resolved serving endpoint name |
| `llm` | retrying `callable(messages, max_tokens=1500) -> str` |
| `log` | a `logging.Logger` |
| `today` | ISO date already baked into `out_base` |

The DAB passes the skill's published volume path via `--skill-dir /Volumes/<catalog>/<schema>/skills/<name>`
(serverless runs the task with no `__file__`, so the path is passed in explicitly).

## branded-pptx (MVP-2 shipped; MVP-3 later)

branded-pptx turns a markdown document into a branded PowerPoint deck. Its faithful engine is
`pptxgenjs` (Node.js) + LibreOffice + Poppler with a multimodal vision-in-the-loop QA cycle -
**none of which run on Databricks Free Edition** (serverless-only: no Node, no system packages).
So the ladder:

- **MVP-2** ([`branded-pptx/`](branded-pptx/), in this repo) re-cuts it in pure-Python `python-pptx`:
  lower fidelity, no vision loop, deterministic and testable. Wiring it into the serverless job
  (python-pptx as a runtime dependency + a live e2e run) is tracked in issue #2.
- **MVP-3** runs it faithfully on a paid tier with classic compute (the self-correcting vision loop).

## Reuse note

Skills are NOT bundled with the DAB: `databricks.yml` excludes `skills/`, and each skill is
published ONCE to a shared Unity Catalog volume (`python scripts/publish_skill.py skills/<name>`),
then consumed by any job via `--skill-dir` - install-once, reuse-everywhere, like
`~/.claude/skills/` in Claude Code. Mechanics and options (folder vs wheel): see
[`../docs/skill-reuse-on-databricks.md`](../docs/skill-reuse-on-databricks.md).
