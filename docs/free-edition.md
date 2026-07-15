# Databricks Free Edition - constraints that shaped this repo

Tested facts on the `coldstart` workspace (2026-07-14). These drive the MVP ladder.

## Compute: serverless-only

Free Edition runs on **serverless compute**. There are no classic clusters (the
`allow-cluster-create` entitlement can appear but classic compute is unavailable).
Consequences:

- No system packages (no `apt`), no Node.js, no LibreOffice/Poppler.
- No init scripts / custom Docker images (those are classic-compute features).
- Job dependencies are Python-only, declared in `databricks.yml` under
  `environments.spec.dependencies`.

This is why the faithful branded-pptx skill (Node `pptxgenjs` + LibreOffice + a vision
QA loop) cannot run here, and why MVP-1 re-cuts it in pure-Python `python-pptx`.

## LLMs: open-weights included, premium models gated

Foundation Model API endpoints are listed in the workspace, but on Free Edition the
premium pay-per-token models are rate-limited to 0 (a billing gate, not a permissions
bug). Tested:

| Endpoint | Free tier |
|----------|-----------|
| `databricks-gpt-oss-120b` | WORKS (reasoning model; content returns as an array of parts) |
| `databricks-glm-5-2` | WORKS (spends token budget on `reasoning_content`; raise max_tokens) |
| `databricks-claude-opus-4-8` | BLOCKED: `rate limit of 0` |
| `databricks-claude-sonnet-5` | BLOCKED: `rate limit of 0` |
| `databricks-gemini-3-5-flash` | BLOCKED: `rate limit of 0` |
| `databricks-gpt-5-6-luna` | BLOCKED: `rate limit of 0` |

FM API endpoints do NOT count against the "1 custom serving endpoint" free-tier limit;
only endpoints you create do. No external API key is used to call them - they run
inside the workspace.

## Windows gotcha

`databricks fs cp /Volumes/...` mangles the path under Git Bash (becomes
`C:\Program Files\Git\Volumes\...`). Use the Python SDK helpers in `scripts/` instead.

## Upgrade path

Enabling a paid tier unlocks the premium models (swap the `llm_endpoint` variable to
`databricks-claude-opus-4-8`) and classic compute (needed for the faithful MVP-2).
