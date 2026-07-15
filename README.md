# Agent Skills on Databricks - a working starter

A minimal, reproducible example of running an AI document pipeline on Databricks:
a scheduled job that reads a document from an **input volume**, transforms it with an
**LLM served inside Databricks** (no external API key), and writes the result to an
**output volume** a user downloads from. Deployed as code via a Databricks Asset Bundle.

The end goal is running full [Anthropic Agent Skills](https://agentskills.io) (like a
"markdown -> branded PowerPoint" skill) on Databricks. This repo builds up to that in
small, honest steps (see [The MVP ladder](#the-mvp-ladder)).

## Mental model (read this first)

```text
  you / a user                Databricks (cloud)                     you / a user
  -----------                 ------------------                     -----------
  drop a document   ->   INBOX volume                                    ^
                              |                                          |
                              v                                          |
                         Lakeflow Job (weekly, or on demand)            |
                              |  calls a model hosted INSIDE Databricks |
                              v                                          |
                         OUTBOX volume  ------ download ----------------+
```

- **Unity Catalog volume** = a governed folder of files. Two of them here:
  `workspace.genai.inbox` (drop documents in) and `workspace.genai.deliverables`
  (pick results up). Paths: `/Volumes/workspace/genai/{inbox,deliverables}`.
- **Lakeflow Job** = tasks + an optional schedule. Ours has one Python task and a
  weekly timer that ships PAUSED (you trigger it by hand until you trust it).
- **LLM inside Databricks** = a Foundation Model API endpoint (`databricks-...`).
  No Anthropic/OpenAI key ever leaves the workspace.
- **Databricks Asset Bundle (DAB)** = infrastructure-as-code. One `databricks.yml`
  describes everything; three commands (`validate`, `deploy`, `run`) manage its life.

## Repo layout

```text
databricks.yml              # THE bundle: variables, the job, the schedule, the target
src/convert_document.py     # the job task: inbox -> LLM -> outbox
samples/weekly-update.md    # a sample INPUT document to convert
skills/                     # where Agent Skills (SKILL.md folders) will live (see skills/README.md)
scripts/setup_uc.py         # one-command Unity Catalog setup (schema + volumes, idempotent)
scripts/upload_input.py     # helper: put a local file into the inbox volume (SDK; Windows-safe)
scripts/download_outputs.py # helper: list/download the outbox volume (SDK; Windows-safe)
scripts/e2e_test.py         # black-box integration test: put -> run -> wait -> get -> assert
docs/free-edition.md        # the tested Free Edition constraints that shaped this repo
requirements-dev.txt        # local dev deps for the helper scripts
.env.example                # config + enterprise private-registry template (copy to .env)
```

## Prerequisites

1. A Databricks workspace (Free Edition is fine) with **Unity Catalog** enabled.
2. **Databricks CLI** v1.5+ (`databricks --version`).
3. An auth profile. This repo assumes one named `coldstart`; create yours with
   `databricks auth login --host https://YOUR-WORKSPACE.cloud.databricks.com`
   and either name it `coldstart` or pass `-p YOURPROFILE` on every command below.
4. Python 3.10+ locally for the helper scripts: `pip install -r requirements-dev.txt`.

## One-time setup: create the volumes

Unity Catalog hierarchy is `catalog -> schema -> volume`. One idempotent command creates
the schema + both volumes (safe to re-run):

```bash
python scripts/setup_uc.py --profile coldstart
# defaults: --catalog workspace --schema genai (must match the databricks.yml variables)
```

## Deploy and run

```bash
# 1) sanity-check the bundle
databricks bundle validate -p coldstart

# 2) create the job in the workspace (uploads src/ + skills/ + samples/)
databricks bundle deploy -p coldstart

# 3) put a document in the inbox (SDK helper avoids Windows path issues)
python scripts/upload_input.py samples/weekly-update.md --profile coldstart

# 4) run the job on demand (weekly schedule stays PAUSED until you unpause it)
databricks bundle run mvp0_weekly_report -p coldstart

# 5) get the result out of the outbox
python scripts/download_outputs.py --profile coldstart
#    -> downloads to ./_outbox/ ; or view in the UI (see below)
```

## Test it end-to-end (no UI needed)

`scripts/e2e_test.py` is a black-box integration test (not a unit test): it treats the
volumes as an S3-like boundary, drops a uniquely-tagged file into the inbox, triggers the
deployed job, waits for a terminal state, retrieves the output, asserts it is correct, and
cleans up. Exit code 0 = pass, 1 = fail (CI-ready).

```bash
databricks bundle deploy -p coldstart      # the test runs against the DEPLOYED job
python scripts/e2e_test.py --profile coldstart
# ... RESULT: PASS - full round-trip in ~40s
```

It resolves the exact job id from this folder's bundle, so it targets the right job even
if another bundle deployed a same-named job. Use `--keep` to leave the test files in place
for inspection.

## See it in the Databricks UI

- **Catalog Explorer** -> `workspace` -> `genai` -> `deliverables`: your output files,
  with a one-click Download. This is the whole delivery story for a Databricks-native user.
- **Workflows** (Lakeflow Jobs) -> the `... GenAI MVP-0 ...` job: run history, logs, the
  schedule. Everything you see here was created by `databricks.yml`, not by hand.

## Swapping the model (free tier vs paid)

The model is a bundle **variable** in `databricks.yml`. On Free Edition, premium
pay-per-token models (Claude, Gemini, GPT-5.x) return `rate limit of 0` (a billing gate),
so the default is the free-tier-callable `databricks-gpt-oss-120b`. When you enable a paid
tier, change one line:

```yaml
variables:
  llm_endpoint:
    default: databricks-claude-opus-4-8   # was databricks-gpt-oss-120b
```

then `databricks bundle deploy` again. Nothing else changes.

## Cleanup

Like an AWS lab, tear everything down when you are done so you leave no trace (and, on a
paid tier, incur no cost). Two tiers - the bundle first, then the data.

### Tier 1: remove the deployed job (safe - no data loss)

`databricks bundle destroy` deletes the resources this bundle created (the job) and its
deployment files. It **requires `--auto-approve`** - a deliberate data-loss guardrail. The
command still prints exactly what will be deleted before it proceeds:

```bash
databricks bundle destroy -p coldstart --auto-approve
# -> "delete resources.jobs.mvp0_weekly_report" + the /Workspace/.../.bundle/... files
```

### Tier 2: remove the data (optional - this IS permanent)

The Unity Catalog schema and volumes are NOT bundle-managed (you made them with
`setup_uc.py`), so `bundle destroy` leaves them and your files intact. To remove them too,
delete the volumes first, then the now-empty schema:

```bash
databricks volumes delete workspace.genai.deliverables -p coldstart
databricks volumes delete workspace.genai.inbox        -p coldstart
databricks schemas delete workspace.genai              -p coldstart
```

### Local scratch (optional)

```bash
rm -rf _outbox _downloaded-report.md .databricks   # regenerated on next run/deploy
```

Nothing here touches the Databricks-hosted models (they are shared platform endpoints), so
there is no model endpoint to delete.

## The MVP ladder

This repo is honest about what works where. It grows in stages:

| MVP | What it does | Status |
|-----|--------------|--------|
| **MVP-0** | inbox doc -> LLM -> outbox doc (proves the pipeline; no skill yet) | working (this repo) |
| **MVP-1** | run the **branded-pptx** skill, re-cut in pure-Python `python-pptx`, to emit a real `.pptx` on free serverless | next |
| **MVP-2** | run branded-pptx **faithfully** (Node + LibreOffice + Claude Agent SDK) | needs a paid tier + classic compute |

Why the staging: branded-pptx's real engine (Node `pptxgenjs` + LibreOffice + a vision QA
loop) cannot run on Databricks Free Edition, which is serverless-only. See
[`skills/README.md`](skills/README.md) for the full explanation.

## Portability notes

- `databricks.yml` is portable except for one workspace-specific line: the `host` under
  `targets.dev.workspace`. A new user changes that to their workspace URL (or deletes it
  and relies on their CLI profile's host).
- `mode: development` namespaces deployed resources per user, so multiple people can deploy
  this bundle into the same workspace without colliding.

## Enterprise / air-gapped environments

Locked-down workspaces have no open PyPI - packages come from an internal Artifactory or
Nexus mirror. The job's Python dependencies (declared in `databricks.yml` under
`environments.spec.dependencies`) then need the standard pip index knobs pointed at your
internal registry:

- `PIP_INDEX_URL` - your internal index (e.g. `https://artifactory.YOURCO.com/.../simple`)
- `PIP_EXTRA_INDEX_URL` - optional fallback
- `PIP_TRUSTED_HOST` - for internal TLS

Set these as environment variables on the compute (see the commented note in
`databricks.yml`), or use a workspace-level private PyPI mirror. Templates are in
`.env.example`. Confirm the exact mechanism with your platform team - it varies by org.

## Troubleshooting

- **`rate limit of 0` when calling a model** - that model is gated on Free Edition. Use
  `databricks-gpt-oss-120b`, or enable a paid tier. See [docs/free-edition.md](docs/free-edition.md).
- **`databricks fs cp` mangles `/Volumes/...` paths on Windows** - use the SDK helpers in
  `scripts/` (they take the path as a plain string).
- **`bundle deploy` says host mismatch** - the `host` under `targets.dev.workspace` in
  `databricks.yml` is the one workspace-specific line; set it to your workspace URL.
- **Renaming `bundle.name`** - run `databricks bundle destroy` under the OLD name first, or
  the previously deployed job is orphaned under the old deployment path.
