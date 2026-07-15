# Concepts

Shared domain vocabulary for this project — entities, named processes, and status concepts with project-specific meaning. Seeded with core domain vocabulary, then accretes as ce-compound and ce-compound-refresh process learnings; direct edits are fine. Glossary only, not a spec or catch-all.

## Skills

### Agent Skill
A portable capability package - a folder with a `SKILL.md` (instructions plus when-to-use metadata) and optional bundled scripts and assets - that an AI agent loads to perform a task. The same open format works across tools; here it runs as a Databricks job.

### Skill runner
The generic, job-side harness that executes a skill: it reads the skill's instructions, runs the skill's deterministic code, calls the LLM for the interpretive parts, and writes the result. The runner supplies the plumbing; the skill supplies the behavior.

### Published skill
A skill that lives once on a shared store (a Unity Catalog volume) and is referenced by any job by path, rather than copied into each job's deployment. Publishing is the install-once step; a consumer job reads it without bundling a copy, so updating the skill in one place reaches every consumer without redeploying them.
*Avoid:* decoupled skill (same idea).

## Models

### Foundation Model API
A Databricks-hosted, pay-per-token LLM endpoint callable by name from inside the workspace, requiring no endpoint creation and no external API key. Distinct from a custom serving endpoint a user provisions; the hosted ones do not count against a workspace's custom-endpoint limit, and premium models may be rate-limited to zero until a paid tier is enabled.

## Governance

### Content guardrail
A screening step - either Databricks AI Gateway configuration or an LLM used as a classifier - that inspects an input for PII or unsafe content and quarantines it instead of processing it.

### Reject queue
The shared store where bad or guardrail-blocked inputs are quarantined, each with a written reason, so a batch run succeeds despite bad records and an operator can inspect what was rejected.
*Avoid:* dead-letter queue (the general term; here it names a specific store with a reason record and batch-succeeds semantics).
