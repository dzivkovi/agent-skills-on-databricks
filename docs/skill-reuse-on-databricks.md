# Reusing a skill across a Databricks workspace (install once, use anywhere)

In Claude Code you install a skill once (`~/.claude/skills/<name>/`) and every project on the
machine can use it. This doc is the Databricks equivalent: how to publish a skill folder ONCE and
have any job/notebook reuse it, updated in one place - instead of bundling a copy into every project.

## What this repo does today (and its limit)

The skill is bundled with the Databricks Asset Bundle, so `bundle deploy` uploads it to
`/Workspace/.../.bundle/<bundle>/files/skills/<name>/` next to the job, and the runner imports it
from there. Simplest for a single project, but: the skill is **duplicated** into every project that
reuses it, and **updating it means redeploying** every consumer. Fine for a hello-world, wrong for
real reuse.

## The mechanisms, ranked (serverless - Free Edition is serverless-only)

| Approach | Install once? | Versioned? | Update flow | Ceremony |
| --- | --- | --- | --- | --- |
| **A. Wheel on a UC Volume, as a job dependency** | yes | yes (wheel version) | rebuild + upload wheel; jobs pin or float | medium |
| **B. Skill folder on a UC Volume + `sys.path.append`** | yes | no (mutable folder) | edit the folder; next run picks it up | low |
| **C. Bundle in the DAB** (current) | no (per-project copy) | via git | redeploy each consumer | lowest |

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
way this repo already imports the skill from its deployed path. No build step, single source of
truth, edit-in-place updates - but no version isolation, so it is best for one team's internal skills.

### C. Keep bundling - for a single project

What this repo does now. Correct when the skill lives with exactly one job and you want the whole
thing to deploy and version together as code.

## Recommendation

- One project, one skill: **keep bundling (C)** - do not add machinery you do not need.
- A skill shared by several jobs, changing occasionally: **UC Volume + `sys.path` (B)**.
- A skill shared widely, needing real versioning and clean updates: **wheel on a UC Volume (A)**.

Migrating this repo to A/B is a small change (publish the skill to a volume, point `--skill-dir` or
the dependency at it) - a good MVP once a second job wants the same skill.

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
