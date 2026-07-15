---
title: "Calling an LLM inside Databricks: Foundation Model APIs (no endpoint to create)"
date: 2026-07-15
category: tooling-decisions
module: llm-invocation (run_skill.py)
problem_type: tooling_decision
component: background_job
severity: medium
applies_when:
  - "Calling an LLM from a Databricks job or notebook"
  - "Wondering why you cannot find the endpoint you are invoking in the Serving UI"
  - "A model call fails with a rate-limit-of-0 error on Free Edition"
symptoms:
  - "PERMISSION_DENIED: The endpoint is temporarily disabled due to a Databricks-set rate limit of 0"
  - "The model you invoke does not appear as an endpoint you created in the Serving UI"
tags: [databricks, foundation-model-api, llm, serving-endpoint, free-edition, rate-limit, ambient-auth]
related_components: [documentation]
---

# Calling an LLM inside Databricks: Foundation Model APIs (no endpoint to create)

## Context

If you come from Model Serving, it is natural to assume you must **create a serving endpoint** before
you can call an LLM. On Databricks you do not - and when a call fails with `rate limit of 0`, it looks
like a permissions bug but is not. Both assumptions send people down the wrong path.

## Guidance

**Call a Foundation Model API endpoint by name.** Databricks pre-hosts a catalog of LLMs; you create
nothing and no external API key ever leaves the workspace.

- Endpoints are named `databricks-*` (e.g. `databricks-gpt-oss-120b`, `databricks-claude-opus-4-8`),
  backed by read-only models in the `system.ai.*` catalog. They already exist in every workspace with
  FM APIs enabled.
- **Invoke** with a POST to `{host}/serving-endpoints/{name}/invocations` using **ambient auth** -
  inside a job the identity is automatic (`WorkspaceClient().config.authenticate()` yields the header).
- These hosted endpoints do **not** count against the "1 custom serving endpoint" limit; only endpoints
  you create do. So they are not "your" endpoints, which is why you will not find them under the ones
  you provisioned.
- **Free Edition gotcha:** premium pay-per-token models (Claude, Gemini, GPT-5.x) return
  `rate limit of 0` - a **billing gate**, not a permissions problem - until a paid tier is enabled. The
  open-weights models (`databricks-gpt-oss-120b`, `databricks-glm-5-2`) work on the free tier.
- **Output-shape gotcha:** reasoning models (e.g. gpt-oss) return `content` as an **array of parts**
  (a reasoning part plus a text part), not a plain string. Handle both shapes when parsing, or you get
  empty output.

## Why This Matters

People waste time trying to provision an endpoint that already exists, or misread `rate limit of 0` as
an entitlement/permission failure and chase the wrong fix (re-auth, roles) instead of the real cause
(the model needs a paid tier). Knowing FM APIs are pre-hosted - call by name, no key, no slot - and
that the rate-limit gate is billing, not permissions, unblocks immediately. The "no external key leaves
the workspace" property is also exactly what makes this attractive for locked-down, governed
environments.

## When to Apply

- Any LLM call from a Databricks job or notebook.
- Free Edition, or any workspace with Foundation Model APIs enabled.
- When a model call returns `rate limit of 0`, or you cannot find the endpoint you are calling.

## Examples

**Invoke with ambient auth** (no key, works inside a job):

```python
w = WorkspaceClient()
host = w.config.host.rstrip("/")
headers = {**w.config.authenticate(), "Content-Type": "application/json"}
resp = requests.post(f"{host}/serving-endpoints/databricks-gpt-oss-120b/invocations",
                     headers=headers, json={"messages": messages, "max_tokens": 800}, timeout=180)
```

**Parse either output shape** (string, or a reasoning model's array of parts):

```python
content = resp.json()["choices"][0]["message"].get("content")
if isinstance(content, list):
    content = "\n".join(p.get("text", "") for p in content if p.get("type") != "reasoning")
```

**The rate-limit gate on Free Edition** (billing, not permissions):

```text
PERMISSION_DENIED: The endpoint is temporarily disabled due to a Databricks-set rate limit of 0.
```

Swapping the model is a one-line change (the endpoint name is a bundle variable), so the day a paid
tier is enabled you point at `databricks-claude-opus-4-8` and nothing else changes.

## Related

- [../../free-edition.md](../../free-edition.md) - which models are callable on Free Edition (tested).
- README section "Where the LLM comes from (there is no endpoint to create)".
- [running-decoupled-agent-skills-on-databricks-serverless.md](../architecture-patterns/running-decoupled-agent-skills-on-databricks-serverless.md) - the skill runner that makes these calls.
