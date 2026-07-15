---
title: Running decoupled Agent Skills on Databricks serverless (UC volume, not bundled)
date: 2026-07-15
category: architecture-patterns
module: skill-runner (run_skill.py + Databricks Asset Bundle)
problem_type: architecture_pattern
component: background_job
severity: medium
applies_when:
  - "Running an Anthropic Agent Skill (SKILL.md + scripts) as a Databricks job"
  - "Reusing one skill across multiple jobs without bundling a copy per job"
  - "On Databricks Free Edition / serverless compute (no Node, no classic clusters)"
symptoms:
  - "NameError: name '__file__' is not defined in a serverless spark_python_task"
  - "A job runs but silently uses the wrong/stale skill path after a run_now parameter override"
tags: [databricks, agent-skills, serverless, unity-catalog-volume, skill-reuse, workspace-file-path, importlib]
related_components: [tooling, documentation]
---

# Running decoupled Agent Skills on Databricks serverless (UC volume, not bundled)

## Context

An Anthropic Agent Skill is a portable folder (`SKILL.md` + `scripts/`). Running one as a
Databricks job raises two questions: (1) how does a serverless job **locate and load** the skill,
and (2) how do you **reuse one skill across many jobs** without bundling a copy into each? The
naive answers hit serverless-specific gotchas, and bundling couples every skill update to a redeploy
of every consumer. This is the working pattern on Free Edition (serverless-only: no Node, no classic
clusters, no init scripts).

## Guidance

**Decouple the skill from the job: publish it ONCE to a shared Unity Catalog volume, and have each
job consume it by path.** This is the Databricks analogue of Claude Code's `~/.claude/skills/`.

1. **Publish (install once)** - upload `SKILL.md` + `scripts/` to `/Volumes/<catalog>/<schema>/skills/<name>/`.
2. **Consume** - pass the job a `--skill-dir` pointing at that volume path; the runner reads `SKILL.md`
   and imports the skill's scripts from there.
3. **Keep it out of the bundle** - `sync.exclude` in `databricks.yml` so `bundle deploy` uploads no copy.

Two serverless gotchas block the naive version:

- **`__file__` is undefined.** A serverless `spark_python_task` runs your file via
  `exec(compile(f.read(), ...))`, which does **not** define `__file__`, so `Path(__file__)` raises
  `NameError`. Do not locate the skill from `__file__` - pass the path in. For a *bundled* skill use
  the DAB substitution `${workspace.file_path}`; for a *decoupled* skill pass the `/Volumes` path.
- **`run_now` wholesale param override silently drops flags.** Triggering the job with
  `jobs.run_now(python_params=[...])` **replaces all** task parameters, dropping `--skill-dir`, so the
  job falls back to a broken default and "runs" against the wrong skill. Read the deployed parameters
  and override only what you must.

## Why This Matters

Bundling the skill into each project duplicates it and makes updating the skill a redeploy of every
consumer. A shared UC volume makes a skill **install-once / reuse-anywhere**, governed by Unity
Catalog, updated in one place (re-run the publish step; consumers pick it up on their next run - no
redeploy). And both gotchas fail in *confusing* ways - a `NameError` deep inside `exec`, and a job
that succeeds while using the wrong skill - so recognizing them saves hours. Note the honest gap:
Databricks has no native "skills directory" auto-discovery like Claude Code; you wire reuse via
`--skill-dir` / dependencies.

## When to Apply

- Running an Agent Skill as a Databricks job on serverless compute.
- More than one job (now or later) will use the same skill.
- Free Edition / serverless (no Node, no system packages, no classic compute).

## Examples

**Publish once** (`scripts/publish_skill.py`, abridged) - walk the local skill folder, upload each
file to the shared volume (auto-creating a `skills` volume):

```python
dest_root = f"/Volumes/{catalog}/{schema}/skills/{name}"
for root, _, files in os.walk(skill_dir):
    for fname in files:
        rel = os.path.relpath(os.path.join(root, fname), skill_dir).replace(os.sep, "/")
        with open(os.path.join(root, fname), "rb") as fh:
            w.files.upload(f"{dest_root}/{rel}", fh, overwrite=True)
```

**Consume from the volume, and keep it out of the bundle** (`databricks.yml`):

```yaml
sync:
  exclude:
    - "skills/"            # the skill is NOT bundled; it comes from the volume
# ... in the job task parameters:
    - "--skill-dir"
    - "/Volumes/${var.catalog}/${var.schema}/skills/document-insights"
```

**Load the skill without `__file__`** (`src/run_skill.py`) - the path arrives as `--skill-dir`;
`importlib` loads a module from a `/Volumes` path fine on serverless (volumes are on the filesystem):

```python
skill_dir = Path(args.skill_dir)                       # NOT Path(__file__)
spec = importlib.util.spec_from_file_location("skill_analyze", skill_dir / "scripts" / "analyze.py")
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
```

**Trigger without dropping deployed flags** (`scripts/e2e_test.py`) - start from the job's deployed
parameters, override only I/O:

```python
deployed = list(w.jobs.get(job_id=job_id).settings.tasks[0].spark_python_task.parameters or [])
params = override(deployed, "--in-path", in_path)      # keeps --skill-dir, --model, --rejected-dir
run = w.jobs.run_now(job_id=job_id, python_params=params).result(...)
```

**Verified** on the live workspace: the deployed bundle top-level contains **no** `skills/` directory,
the skill exists only on the volume, and the black-box `e2e_test.py` passes - i.e. the job ran a skill
it does not bundle, loaded from the volume. Update flow: edit the skill, re-run `publish_skill.py`,
next run picks it up.

## Related

- GitHub issue #1 - `feat(reuse): publish a skill once to a UC volume and consume it unbundled`.
- [../../skill-reuse-on-databricks.md](../../skill-reuse-on-databricks.md) - the fuller options (folder + `sys.path`, wheel dependency, or bundling) with source links.
- [../../free-edition.md](../../free-edition.md) - the serverless-only constraints that shape this.
