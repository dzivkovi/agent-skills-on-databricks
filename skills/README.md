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

## The boundary: your skill is read-only

**You change nothing about a skill to run it here.** Every folder in `skills/` is byte-identical
to what sits in `~/.claude/skills/` - `SKILL.md` plus the skill's own `scripts/`. No Databricks
import, no wrapper, no registration. That is the claim this repo has to keep, so it is worth
being blunt about where the line falls:

| | Owns | May contain Databricks-specific code? |
| --- | --- | --- |
| **The skill** (`skills/<name>/`) | `SKILL.md`, its own `scripts/` | **No. Never.** Read-only. |
| **The harness** (`src/`, `databricks.yml`) | the runner, the adapters, the job config | Yes - this is what "harness" means |

When Databricks throws an obstacle at you, it belongs in the harness. Every time. The volume
zip trap in [`../src/adapters.py`](../src/adapters.py) is the worked example: the fix is four
lines, it lives in an adapter, and `branded-pptx` never learned it existed.

## How a job runs a skill

[`../src/run_skill.py`](../src/run_skill.py) is the runner, and it owns only the plumbing common
to every skill: reading the input, the structural and content guards (bad or blocked input goes
to the reject queue instead of failing the batch), resolving the model from `SKILL.md`, a
retrying inside-Databricks LLM client, and the output naming.

It never hardcodes a report shape. The shaping lives in an **adapter** in
[`../src/adapters.py`](../src/adapters.py), and the **job** picks one with `--adapter`:

| adapter | for a skill whose deterministic half is | produces |
| --- | --- | --- |
| `report` (default) | `scripts/analyze.py` returning a dict of exact metrics | a labeled markdown report (metrics table + LLM reading) |
| `deck` | `scripts/build_pptx.py` | a real `.pptx`, no LLM call |

Adapter choice is a fact about the platform, not about the skill, which is why the job declares
it and `SKILL.md` does not. Adding a skill of an existing shape costs **nothing**: publish the
folder, point `--skill-dir` at it. Only a genuinely new output shape needs a new adapter, and it
goes in the harness with the others.

Each adapter receives a frozen `ctx` dict:

| key | what it is |
| --- | --- |
| `text` | the full input document text (guards already passed) |
| `in_path` | source file path (use only the basename for provenance lines) |
| `out_dir` | destination directory, already created |
| `out_base` | `<out_dir>/<stem>-<skill>-<date>` with no extension - the adapter appends `.md` or `.pptx` |
| `skill_dir` | path to the skill folder on this filesystem (the adapter loads the skill's scripts from here, by path) |
| `skill_md` | full `SKILL.md` text |
| `skill_name` | skill folder basename, e.g. `document-insights` |
| `model` | resolved serving endpoint name |
| `llm` | retrying `callable(messages, max_tokens=1500) -> str` |
| `log` | a `logging.Logger` |
| `today` | ISO date already baked into `out_base` |

The DAB passes the skill's published volume path via `--skill-dir /Volumes/<catalog>/<schema>/skills/<name>`
(serverless runs the task with no `__file__`, so the path is passed in explicitly).

## branded-pptx (MVP-2 shipped; MVP-4 needs paid compute)

branded-pptx turns a markdown document into a branded PowerPoint deck. Its faithful engine is
`pptxgenjs` (Node.js) + LibreOffice + Poppler with a multimodal vision-in-the-loop QA cycle -
**none of which run on Databricks Free Edition** (serverless-only: no Node, no system packages).
So the ladder:

- **MVP-2** ([`branded-pptx/`](branded-pptx/), in this repo) re-cuts it in pure-Python `python-pptx`:
  lower fidelity, no vision loop, deterministic and testable. Live on serverless.
- **MVP-4** runs it faithfully on a paid tier with classic compute (the self-correcting vision loop).

MVP-3 sits between them and is not about this skill at all: it is composition, publishing skills
once to a shared volume and chaining them into one job. That is why MVP-4 is the fidelity rung.

## Reuse note

Skills are NOT bundled with the DAB: `databricks.yml` excludes `skills/`, and each skill is
published ONCE to a shared Unity Catalog volume (`python scripts/publish_skill.py skills/<name>`),
then consumed by any job via `--skill-dir` - install-once, reuse-everywhere, like
`~/.claude/skills/` in Claude Code. Mechanics and options (folder vs wheel): see
[`../docs/skill-reuse-on-databricks.md`](../docs/skill-reuse-on-databricks.md).
