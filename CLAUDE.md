# CLAUDE.md - agent working notes for this repo

## The boundary (read this before touching skills/)

An imported skill is READ-ONLY: `skills/<name>/` is byte-identical to what a Claude Code user
has, and nothing Databricks-specific may be added to it - no wrapper, no import, no
registration. All platform code lives in the harness (`src/run_skill.py` plumbing,
`src/adapters.py` shaping, `databricks.yml` config). When Databricks throws an obstacle, the
harness absorbs it; the skill never learns. This repo is teaching material, and that boundary is
the lesson - do not erode it for convenience.

Adapter choice is harness config: the job passes `--adapter report|deck`. It is never declared
in SKILL.md, because which adapter to use is a fact about the platform, not the skill. A skill of
an existing shape costs nothing to add (publish the folder, point --skill-dir at it); only a new
output SHAPE needs a new adapter, in src/adapters.py.

## Definition of done

A feature is done only when the DEPLOYED system passes the full smoke suite, not when unit
tests are green:

    python scripts/smoke.py --profile coldstart    # pytest + every scripts/e2e_*.py, live

Every new feature with a runtime surface adds (or extends) a scripts/e2e_*.py suite; smoke.py
auto-discovers them, so coverage cannot silently lag the feature list.

Never let a PR say "Closes #N" on local proof alone. If any acceptance criterion or premise is
unverified against the deployed system, the PR says "Refs #N" and the ticket stays open until
a live run proves it. (Lesson of 2026-07-16: "Closes #2" shipped while the skill had never run
on Databricks; the owner discovered the gap from a closed tracker.)

## Rollout order (skills are volume-published, not bundled)

databricks.yml excludes skills/ from the bundle; skills live on a shared UC volume. After
changing a skill, republish it: python scripts/publish_skill.py skills/<name> --profile coldstart.
When a change spans the runner AND skills, republish the skills FIRST (additive - the old
runner ignores new files), then databricks bundle deploy.

## Chaining skills (the report_to_deck job)

A chain is not a new program: it is two runs of the SAME runner with different `--skill-dir`.
The upstream task writes a run-scoped manifest (`--manifest-out .../_runs/{{job.run_id}}/manifest.json`),
the downstream task reads it (`--manifest-in <same path>`) and works on whatever the upstream
produced. The manifest carries STATUS, not just a path, so a quarantined input makes the
downstream task SKIP and succeed - the reject queue's promise held across a chain.

Do not reach for the alternatives: `dbutils.jobs.taskValues` is documented notebook-only and
does not exist in a `spark_python_task`; recomputing the upstream's dated filename breaks when a
run straddles UTC midnight; glob-newest is brittle under retries and concurrent runs.

A multi-task job must be triggered with JOB parameters (`run_now(job_parameters={...})`), never
`python_params` - the latter pushes identical argv into every task, and chained tasks by
definition need different arguments.

Only `ok` (which must carry a `report_path`) and `rejected` are legal statuses; a downstream task
raises on anything else rather than skipping, because a malformed manifest that read as a clean
skip would be a silent no-output success. Note the downstream task re-guards its input, which for
a chain is the upstream's generated report (not the original document - the report carries metrics
plus an LLM reading, never the raw source). Guarding a derived artifact again is accepted on
purpose: it is the artifact a human opens, and a rejection there behaves like any other - the
report is quarantined with a reason and the batch still succeeds.

## Unity Catalog volume traps

Volumes support sequential writes, NOT random access. Anything that seeks while writing - a zip
archive, and therefore any .pptx/.xlsx/.docx - fails at close() with `OSError errno 5` (I/O
error) or `errno 95` (operation not supported) when written straight to `/Volumes/...`. Build
such artifacts in memory (BytesIO) or on local disk, then write the finished bytes in one
sequential write. Plain markdown/text writes are unaffected. Found live in the #2 e2e; the fix
belongs in the skill's own `scripts/run.py` adapter, never in the portable builder or the runner.

## Git Bash traps (Windows)

- Volume paths need the scheme prefix: databricks fs ls dbfs:/Volumes/... (a bare /Volumes/...
  path gets mangled by Git Bash, and MSYS_NO_PATHCONV=1 does not fix it).
- databricks bundle run <job> -p <profile> -- --flag value REPLACES the deployed task
  parameters wholesale (no merge). To override selectively, read the deployed params first the
  way scripts/e2e_test.py does.
