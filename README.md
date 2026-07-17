# Agent Skills on Databricks: run Anthropic SKILL.md as a governed Lakeflow job

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
  drop a document   ->   INPUT volume                                    ^
                              |                                          |
                              v                                          |
                         Lakeflow Job (weekly, or on demand)             |
                              |  calls a model hosted INSIDE Databricks  |
                              v                                          |
                         OUTPUT volume  ------ download -----------------+
```

- **Unity Catalog volume** = a governed folder of files. Three here: `workspace.genai.input`
  (drop documents in), `workspace.genai.output` (pick results up), and `workspace.genai.rejected`
  (a dead-letter queue - bad/blocked inputs land here without failing the batch; see
  [docs/guardrails-and-dead-letter-queue.md](docs/guardrails-and-dead-letter-queue.md)).
- **Lakeflow Job** = tasks + an optional schedule. Ours has one Python task and a
  weekly timer that ships PAUSED (you trigger it by hand until you trust it).
- **LLM inside Databricks** = a Foundation Model API endpoint (`databricks-...`).
  No Anthropic/OpenAI key ever leaves the workspace.
- **Databricks Asset Bundle (DAB)** = infrastructure-as-code. One `databricks.yml`
  describes everything; three commands (`validate`, `deploy`, `run`) manage its life.

## Repo layout

```text
databricks.yml              # THE bundle: variables, the jobs, the schedule, the target
src/run_skill.py            # the harness: plumbing (input, guards, reject queue, model, LLM)
src/adapters.py             # the harness: shapes the output. ALL Databricks-specific code lives here
skills/<name>/              # skills, EXACTLY as imported from Claude Code. Read-only, never modified
samples/weekly-update.md    # a sample INPUT document to analyze
scripts/setup_uc.py         # one-command Unity Catalog setup (schema + volumes, idempotent)
scripts/publish_skill.py    # publish a skill ONCE to a shared UC volume (reuse it unbundled)
scripts/smoke.py            # THE gate: pytest + every e2e_*.py against the DEPLOYED system
scripts/upload_input.py     # helper: put a local file into the input volume (SDK; Windows-safe)
scripts/download_outputs.py # helper: list/download the output volume (SDK; Windows-safe)
scripts/e2e_*.py            # live end-to-end suites; smoke.py auto-discovers them
docs/migrating-a-skill.md   # START HERE to bring your own skill: the boundary, and the traps
docs/free-edition.md        # the tested Free Edition constraints that shaped this repo
requirements-dev.txt        # local dev deps for the helper scripts
.env.example                # config + enterprise private-registry template (copy to .env)
```

**Bringing your own skill?** Read [docs/migrating-a-skill.md](docs/migrating-a-skill.md). The
short version: you change nothing about your skill - everything Databricks-specific lives in the
harness.

## Prerequisites

1. A Databricks workspace (Free Edition is fine) with **Unity Catalog** enabled.
2. **Databricks CLI** with Asset Bundle support (check with `databricks version`).
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

# 2) create the job in the workspace (the SKILL is NOT bundled - it comes from a volume, step 3)
databricks bundle deploy -p coldstart

# 3) publish the skill ONCE to a shared UC volume; the job consumes it there (reuse, not bundling)
python scripts/publish_skill.py skills/document-insights --profile coldstart

# 4) put a document in the input (SDK helper avoids Windows path issues)
python scripts/upload_input.py samples/weekly-update.md --profile coldstart

# 5) run the job on demand (weekly schedule stays PAUSED until you unpause it)
databricks bundle run mvp0_weekly_report -p coldstart

# 6) get the result out of the output
python scripts/download_outputs.py --profile coldstart
#    -> downloads to ./_output/ ; or view in the UI (see below)
```

## Test it end-to-end (no UI needed)

`scripts/e2e_test.py` is a black-box integration test (not a unit test): it treats the
volumes as an S3-like boundary, drops a uniquely-tagged file into the input, triggers the
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

For the **negative path**, `python scripts/e2e_reject_test.py --profile coldstart` proves that bad
inputs (empty, and a document with fake PII the LLM content guardrail flags) are quarantined to the
reject queue while the job still returns SUCCESS. See
[docs/guardrails-and-dead-letter-queue.md](docs/guardrails-and-dead-letter-queue.md).

## See it in the Databricks UI

**Find your volumes (works in any workspace):** in the left sidebar click **Catalog** (the
database/cylinder icon), then expand your catalog -> schema -> **Volumes** -> the volume ->
its **Files** area. Files there have a one-click **Download** - this is the whole delivery
story for a Databricks-native user. The direct URL pattern is:

```text
https://<your-workspace-host>/explore/data/volumes/<catalog>/<schema>/<volume>
```

For this repo's defaults that is `.../explore/data/volumes/workspace/genai/output` (results)
and `.../input` (drop zone). Swap `<your-workspace-host>` for your own workspace URL.

**Watch the job:** left sidebar -> **Workflows** (Lakeflow Jobs) -> the `... document-insights skill`
job for run history, logs, and the schedule. Everything you see there was created by
`databricks.yml`, not by hand. (Its bundle resource key is still `mvp0_weekly_report`, which is
why `bundle run mvp0_weekly_report` above is correct - the key is legacy, the display name is current.)

## Where the LLM comes from (there is no endpoint to create)

The surprising part if you are used to Model Serving: **you never created the LLM endpoint, and
you never will.** `databricks-gpt-oss-120b` is a
**[Foundation Model API](https://docs.databricks.com/aws/en/machine-learning/foundation-model-apis)** -
a catalog of models Databricks pre-hosts and manages for you, billed per token, addressable by
name. Your workspace already has ~18 of them - see the
[full, frequently-updated list of hosted models](https://docs.databricks.com/aws/en/machine-learning/foundation-model-apis/supported-models)
(`databricks-claude-opus-4-8`, `databricks-glm-5-2`, embedding models, ...) - each backed by a
read-only model in the `system.ai.*` catalog. No cluster, no config, no endpoint slot used.

- **Find it in the UI:** left nav **Serving** -> the endpoints list -> search `databricks-gpt-oss-120b`.
  It sits among the other `databricks-*` foundation models. These are distinct from a **custom
  endpoint** you create yourself (like a `my-echo-endpoint`), which is what you normally provision
  and which counts against the Free Edition limit of one. Foundation Model APIs do not.
- **How the job calls it:** a POST to `https://<host>/serving-endpoints/databricks-gpt-oss-120b/invocations`
  with the job's ambient identity (see [`src/run_skill.py`](src/run_skill.py)). Nothing to deploy.
- **Free-tier caveat:** premium models (`databricks-claude-*`, `databricks-gemini-*`,
  `databricks-gpt-5-6-*`) return `rate limit of 0` (a
  [Foundation Model APIs quota](https://docs.databricks.com/aws/en/machine-learning/foundation-model-apis/limits))
  until you enable a paid tier; the open-weights ones (`databricks-gpt-oss-120b`,
  `databricks-glm-5-2`) work today. See [docs/free-edition.md](docs/free-edition.md).

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
`setup_uc.py`, plus a `skills` volume from `publish_skill.py`), so `bundle destroy` leaves them
and your files intact. `schemas delete` refuses a non-empty schema, so delete ALL its volumes
first (input, output, rejected, and skills), then the now-empty schema:

```bash
databricks volumes delete workspace.genai.output   -p coldstart
databricks volumes delete workspace.genai.input    -p coldstart
databricks volumes delete workspace.genai.rejected -p coldstart
databricks volumes delete workspace.genai.skills   -p coldstart
databricks schemas delete workspace.genai          -p coldstart
```

### Local scratch (optional)

```bash
rm -rf _output _downloaded-report.md .databricks   # regenerated on next run/deploy
```

Nothing here touches the Databricks-hosted models (they are shared platform endpoints), so
there is no model endpoint to delete.

## Run the skill in Claude Code first (portability litmus test, no zip)

Agent Skills are an open standard, so the same `skills/document-insights/` folder that runs on
Databricks also runs in Claude Code on your laptop. Install it there first - **if the skill does
not trigger in Claude Code, it will not work in Databricks or anywhere else.** There is no
zipping or packaging: Claude Code discovers a skill from a folder on its skills path.

Claude Code looks in `.claude/skills/` (this project) and `~/.claude/skills/` (personal, all
projects). Point that path at this repo's canonical skill in `skills/` - ideally a **link**, so
there is one source of truth rather than a copy. `.claude/skills/` is gitignored here (a
per-developer install; the committed skill stays in `skills/`).

```bash
# macOS / Linux - a real symlink (Claude Code follows it, zero duplication):
mkdir -p .claude/skills
ln -s ../../skills/document-insights .claude/skills/document-insights

# Windows - heads up: `ln -s` in Git Bash SILENTLY COPIES (no link) unless Developer Mode is on.
# For a true no-duplication link with NO admin rights, use a directory junction:
#   cmd /c mklink /J .claude\skills\document-insights skills\document-insights
# ...or just accept a copy (simplest, but duplicates):
#   cp -r skills/document-insights .claude/skills/
```

Then **restart Claude Code** (a newly created top-level skills dir is only watched after a
restart). Now type `/document-insights`, or just ask: "analyze samples/weekly-update.md with the
document-insights skill." If Claude runs `analyze.py` and returns the exact metrics plus a
sentiment read, the skill is sound - and portable.

## The MVP ladder

This repo is honest about what works where. It grows in stages:

| MVP | What it does | Status |
| --- | --- | --- |
| **MVP-0** | input doc -> LLM -> output doc (plumbing only, no skill) | done |
| **MVP-1** | run a real skill: **document-insights** - deterministic metrics (code) + sentiment/themes (LLM), labeled by half | working (this repo) |
| **MVP-2** | the **branded-pptx** skill, re-cut in pure-Python `python-pptx`, to emit a real `.pptx` on free serverless | next |
| **MVP-3** | run branded-pptx **faithfully** - the self-correcting vision-in-the-loop QA (Node + LibreOffice) | needs a paid tier + classic compute |

Why the staging: branded-pptx's real engine (Node `pptxgenjs` + LibreOffice + a vision QA
loop) cannot run on Databricks Free Edition, which is serverless-only. See
[`skills/README.md`](skills/README.md) for the full explanation.

### How the LLM and agents are invoked here (pure Databricks)

The model is called the **Databricks-native** way: the job hits a Databricks-served endpoint
through the Databricks SDK (`WorkspaceClient`) - no external API key, no vendor client library.
To expose a skill as a *queryable, governed agent* (instead of a batch job), the Databricks path
is its **own Mosaic AI Agent Framework**: wrap the logic in an MLflow `ResponsesAgent`, publish
with `databricks.agents.deploy(...)`, and query it with the Databricks OpenAI client - all
inside Unity Catalog. The **Claude Agent SDK is not part of this** - it is an Anthropic laptop
harness; on Databricks the model is always served within the platform, so there is no vendor
intermixing at the LLM layer. Full fact-check in
[docs/agent-invocation-on-databricks.md](docs/agent-invocation-on-databricks.md).

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

## References

The Databricks docs behind everything above - these pages update often, so trust the source, not
this README:

- **Foundation Model APIs** (the LLMs you call by name):
  [overview](https://docs.databricks.com/aws/en/machine-learning/foundation-model-apis) ·
  [full list of hosted models](https://docs.databricks.com/aws/en/machine-learning/foundation-model-apis/supported-models) ·
  [rate limits and quotas](https://docs.databricks.com/aws/en/machine-learning/foundation-model-apis/limits) ·
  [REST API reference](https://docs.databricks.com/aws/en/machine-learning/foundation-model-apis/api-reference)
- **Model Serving** (how endpoints are queried):
  [query a serving endpoint](https://docs.databricks.com/api/workspace/servingendpoints/query) ·
  [Anthropic Messages API on Databricks](https://docs.databricks.com/aws/en/machine-learning/model-serving/query-anthropic-messages)
- **Databricks Asset Bundles / DABs** (the `databricks.yml` deploy model):
  [resources](https://docs.databricks.com/aws/en/dev-tools/bundles/resources) ·
  [examples](https://docs.databricks.com/aws/en/dev-tools/bundles/examples) ·
  [bundle CLI](https://docs.databricks.com/aws/en/dev-tools/cli/bundle-commands)
- **Unity Catalog Volumes** (the input/output file stores):
  [volumes](https://docs.databricks.com/aws/en/volumes/)
- **Agent Skills** (the open `SKILL.md` standard):
  [agentskills.io](https://agentskills.io) ·
  [Claude Code skills docs](https://code.claude.com/docs/en/skills)
- **Deploying agents the Databricks way** (Mosaic AI Agent Framework, and why not the Claude Agent SDK):
  see [docs/agent-invocation-on-databricks.md](docs/agent-invocation-on-databricks.md)
- **Guardrails and a reject queue** (platform PII/safety guardrails + the dead-letter pattern):
  see [docs/guardrails-and-dead-letter-queue.md](docs/guardrails-and-dead-letter-queue.md)
- **Reusing a skill across the workspace** (install once via a UC-Volume wheel, not bundled per project):
  see [docs/skill-reuse-on-databricks.md](docs/skill-reuse-on-databricks.md)
- **Free Edition limits** (serverless-only, which models work): sourced in
  [docs/free-edition.md](docs/free-edition.md)
- **Knowledge base** (learnings from building this, worth reading before working in a documented area):
  [docs/solutions/](docs/solutions/) - documented patterns/gotchas with YAML frontmatter (`module`, `tags`,
  `problem_type`); [CONCEPTS.md](CONCEPTS.md) - shared domain vocabulary
