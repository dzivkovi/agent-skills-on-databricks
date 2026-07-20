# Reusing a skill across a Databricks workspace (install once, use anywhere)

In Claude Code you install a skill once (`~/.claude/skills/<name>/`) and every project on the
machine can use it. This doc is the Databricks equivalent: how to publish a skill folder ONCE and
have any job/notebook reuse it, updated in one place - instead of bundling a copy into every project.

## What this repo does today

Skills are **published once to a shared Unity Catalog volume** and consumed from there by path
(`--skill-dir /Volumes/<catalog>/<schema>/skills/<name>`). `databricks.yml` excludes `skills/`
from the bundle entirely, so the deployed job carries no copy. Edit a skill, re-run
`scripts/publish_skill.py`, and the next run of every consumer picks it up with no redeploy.

That is option B below. The repo started on option C (bundling a copy per project), which
duplicated the skill into every consumer and required a redeploy to update one - fine for a
hello-world, wrong for real reuse. Option B replaced it.

## The mechanisms, ranked (serverless - Free Edition is serverless-only)

| Approach | Install once? | Versioned? | Update flow | Ceremony |
| --- | --- | --- | --- | --- |
| **A. Wheel on a UC Volume, as a job dependency** | yes | yes (wheel version) | rebuild + upload wheel; jobs pin or float | medium |
| **B. Skill folder on a UC Volume** (current) | yes | no (mutable folder) | edit the folder; next run picks it up | low |
| **C. Bundle in the DAB** (what this repo started with) | no (per-project copy) | via git | redeploy each consumer | lowest |

### A. Recommended for real reuse - a wheel on a shared UC Volume

Package the skill's `scripts/` as a proper Python wheel (ship `SKILL.md` as package data, read via
`importlib.resources`). Publish the `.whl` once to a shared volume, e.g.
`/Volumes/<catalog>/<schema>/skills/document_insights-0.1.0-py3-none-any.whl`. Any serverless job
then depends on it:

```yaml
environments:
  - environment_key: serverless_env
    spec:
      environment_version: "3"
      dependencies:
        - /Volumes/workspace/genai/skills/document_insights-0.1.0-py3-none-any.whl
```

The job just `import document_insights` - no path juggling. This is the idiomatic Databricks
"install once, depend anywhere": serverless dependencies accept a wheel (or a project dir with
`pyproject.toml`) from a UC volume or workspace file.

- **Update flow:** bump the version, build, upload the new `.whl`; consumers that float to the
  latest pick it up on their next run, consumers that pin a version upgrade deliberately.
- **Governance:** the volume is a Unity Catalog object - permissions and lineage come for free.

### B. Low-ceremony middle ground - a folder on a UC Volume

Put the skill folder on a shared volume (`/Volumes/<catalog>/<schema>/skills/<name>/`) and in the
job:

```python
import sys
sys.path.append("/Volumes/workspace/genai/skills/document-insights/scripts")
from analyze import analyze          # the skill's deterministic code
skill_md = open("/Volumes/workspace/genai/skills/document-insights/SKILL.md").read()
```

Volumes are on the job's filesystem, so importing a module from a `/Volumes` path works the same
way this repo imports the skill. No build step, single source of truth, edit-in-place updates - but
no version isolation, so it is best for one team's internal skills.

**This repo now uses option B.** [`scripts/publish_skill.py`](../scripts/publish_skill.py) publishes
the skill to `/Volumes/<catalog>/<schema>/skills/<name>/`, the job's `--skill-dir` points there, and
`skills/` is excluded from the bundle via `sync.exclude` in `databricks.yml`. Verified: the deployed
bundle contains no skill copy, yet the job runs - it consumes the skill from the volume. Update flow:
edit the skill, re-run `publish_skill.py`, and the next job run picks it up with no redeploy.

### C. Keep bundling - for a single project

What this repo started with, and still the right answer when a skill lives with exactly one job
and you want the whole thing to deploy and version together as code.

## Security: the skills volume is a code-execution trust boundary

Read this before anyone asks. Decoupling the skill onto a shared volume is convenient, but it
moves a trust boundary: the runner imports and **executes** the skill's `scripts/analyze.py`
from `--skill-dir` (see the adapters in [`src/adapters.py`](../src/adapters.py)). Whoever
can WRITE that volume path can run arbitrary code inside the job's identity - read the workspace,
reach any endpoint the job can, exfiltrate the input documents. With the bundled option (C) the
skill is reviewed and shipped as code; with A/B the gate becomes the volume's Unity Catalog ACL.

What that means in practice, and what makes this acceptable internally:

- **Lock the write ACL.** Grant `WRITE VOLUME` on `.../skills` only to the people/service principal
  that publishes skills; everyone else gets read. The job needs only read. This is the actual
  control - "who can publish" is "who can run code," so treat it like write access to a deploy
  pipeline. Consumers being able to read is fine; the danger is an unexpected writer.
- **Keep publishing a deliberate, owned step.** `publish_skill.py` is the one door onto the volume.
  Run it from a trusted place (CI or an owner's session), not ad hoc from wherever.
- **Vet a skill before you publish it** - the same way you would not `pip install` an unreviewed
  wheel onto prod. Today that is a human code review of `SKILL.md` + `scripts/`; an automated
  pre-publish scan (static checks, an allowlist of imports, no network/`subprocess`/`eval` in a
  skill's deterministic half) is the natural next step and is tracked as a backlog item, not built
  here. That scan is what turns "pluggable" from a worry into a policy.
- **Prompt injection is a separate, known limitation** of the LLM content guardrail (not the skill
  loader) - see [`guardrails-and-dead-letter-queue.md`](guardrails-and-dead-letter-queue.md).

For an untrusted, multi-tenant setup, prefer option A (a versioned wheel you build and sign in CI)
or keep skills bundled (C) so they ride the reviewed deploy path. For a single team's internal
skills on a locked-down volume, B is a reasonable, governed choice.

## Recommendation

- One project, one skill: **keep bundling (C)** - do not add machinery you do not need.
- A skill shared by several jobs, changing occasionally: **UC Volume + `sys.path` (B)**.
- A skill shared widely, needing real versioning and clean updates: **wheel on a UC Volume (A)**.

This repo made that move: it runs on B, because a second and third skill arrived and two jobs now
consume them. Going further to A (a versioned, signed wheel) is the step worth taking when skills
are shared beyond one team, or when an untrusted publisher is possible.

## Rough edges vs Claude Code (support-ticket / feature-request candidates)

- **No native skills registry.** Claude Code auto-discovers `SKILL.md` folders from a known
  directory with hot-reload and zero config. Databricks has no first-class "skill" concept on
  compute - you reuse code via dependencies/paths, and `SKILL.md` is just a data file you read. A
  workspace-level "skills you can reference by name" registry would close the gap.
- **No hot-reload of a bundled skill** - a redeploy is required (options A/B avoid this by moving the
  skill off the bundle).

## Sources

- Configure the serverless environment (dependencies from a UC volume / workspace file): https://docs.databricks.com/aws/en/compute/serverless/dependencies
- Install libraries (wheel / requirements from supported locations): https://docs.databricks.com/aws/en/libraries/
- Securing Python dependencies on serverless with UC Volumes: https://community.databricks.com/t5/technical-blog/securing-python-dependencies-on-databricks-serverless-with-unity/ba-p/133688
- Work with files in Unity Catalog volumes: https://docs.databricks.com/aws/en/volumes/volume-files
