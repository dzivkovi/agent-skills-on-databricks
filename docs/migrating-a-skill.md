# Bring your own skill to Databricks

You have an Agent Skill that works in Claude Code. This is how it runs as a scheduled Databricks
job, on Free Edition, with no external API key.

**The short version: you change nothing about your skill.**

## The boundary

This is the whole idea, so it is worth one table:

| | Owns | May contain Databricks-specific code? |
| --- | --- | --- |
| **Your skill** (`skills/<name>/`) | `SKILL.md`, its own `scripts/` | **No. Never.** Read-only. |
| **The harness** (`src/`, `databricks.yml`) | the runner, the adapters, the job config | Yes - that is what "harness" means |

The skill folders in this repo are the same folders you would drop in `~/.claude/skills/`: a
`SKILL.md` and the skill's own scripts. No wrapper, no import, no registration. When Databricks
throws an obstacle at you, **the harness absorbs it and your skill never learns**. That rule is
what keeps a skill portable, and this repo enforces it with a test
([`tests/test_skills_contract.py`](../tests/test_skills_contract.py) fails if harness code
appears inside a skill).

## Migrate it in four commands

```bash
cp -r ~/.claude/skills/my-skill skills/my-skill      # 1. copy it in. Change nothing.
python scripts/setup_uc.py --profile coldstart        # 2. once per workspace: schema + volumes
python scripts/publish_skill.py skills/my-skill --profile coldstart   # 3. publish to the volume
databricks bundle deploy -p coldstart                 # 4. deploy the job
```

Then run it against a document already in the input volume:

```bash
MSYS_NO_PATHCONV=1 databricks bundle run mvp0_weekly_report -p coldstart -- \
  --skill-dir /Volumes/workspace/genai/skills/my-skill
```

Publishing is separate from deploying **on purpose**: a skill lives once on a shared Unity
Catalog volume and any job reads it by path. Edit the skill, re-publish, and every consumer picks
it up with no redeploy. That is the Databricks equivalent of `~/.claude/skills/` being shared by
every project - see [skill-reuse-on-databricks.md](skill-reuse-on-databricks.md).

## Will your skill fit?

The harness runs your skill through an **adapter** ([`../src/adapters.py`](../src/adapters.py)),
and the job picks which one with `--adapter`:

| `--adapter` | Your skill's deterministic half | You get |
| --- | --- | --- |
| `report` (default) | `scripts/analyze.py` with `analyze(text) -> dict` | a markdown report: your exact metrics as a table, plus an LLM reading guided by your `SKILL.md` |
| `deck` | `scripts/build_pptx.py` with `build_deck(md, out)` | a real `.pptx` |

If your skill matches a shape, migrating costs **nothing**: publish the folder, point
`--skill-dir` at it. If it does not, you write **one adapter in the harness** - and your skill
still does not change. One shape per skill; the job says which.

The adapter and the skill must agree. Point `--adapter report` at a skill with no `analyze.py`
and the harness tells you exactly that, by name. That is deliberate: an explicit job that reads
"this skill through that adapter" teaches better than magic that guesses wrong quietly.

## The traps that will actually bite you

Every one of these cost us a real failed run. All of them are the harness's problem, not your
skill's.

**Office files cannot be written to a volume.** A `.pptx` is a zip, and zipfile *seeks* while
packing. Unity Catalog volumes take sequential writes only, so saving straight to `/Volumes/...`
dies at `close()` with `OSError errno 5` or `errno 95`. The same is true of `.docx` and `.xlsx` -
every Office format is a zip. **Build it in memory, then write the finished bytes once**
(four lines, in the `deck` adapter). Markdown, text and PDF are unaffected: they write straight
through. Note what this did NOT require: no patch to `python-pptx`, and no change to the skill.

**The job may not mutate your published skill.** Importing a skill's script normally lets Python
drop a `__pycache__` next to it *on the volume*. The adapter suppresses bytecode for that import,
so "read-only" stays literally true.

**Your harness can be more than one file.** A serverless `spark_python_task` imports sibling
modules from the bundle, so `from adapters import ADAPTERS` works. We verified this with a live
run rather than assuming it.

**On Windows Git Bash, volume paths need a scheme prefix.** `databricks fs ls dbfs:/Volumes/...`
works; a bare `/Volumes/...` gets mangled into `C:\Program Files\Git\Volumes\...`, and
`MSYS_NO_PATHCONV=1` does *not* fix it (the SDK-based helper scripts sidestep this entirely).

**`bundle run ... -- --flag value` REPLACES every deployed parameter**, it does not merge. To
override selectively, read the deployed params first, the way
[`../scripts/e2e_test.py`](../scripts/e2e_test.py) does.

## One judgement call you should make deliberately

Your `SKILL.md` front-matter may pin a model, and the harness honors it unless the job passes
`--model`:

```yaml
metadata:
  model: databricks-gpt-oss-120b
```

`readability` does this to demonstrate per-skill model choice - **and it is a real tradeoff worth
seeing.** A serving endpoint name is Databricks-specific, so pinning one couples an otherwise
portable skill to this platform. Reading `SKILL.md` is always fine; the question is whether that
*value* belongs in a folder you want to carry elsewhere. Prefer letting the job decide, unless a
skill genuinely needs a particular model to be correct.

## Prove it, don't hope

```bash
python scripts/smoke.py --profile coldstart
```

pytest plus every end-to-end suite, against the **deployed** system. Green local tests are not
migration proof: branded-pptx had seven passing local tests and still failed its first real run
on the volume-zip trap above. If your skill matters, it gets an `e2e_*.py`; smoke discovers it
automatically.
