# How LLMs and agents are invoked on Databricks (fact-check)

Short version: **on Databricks you use Databricks' own SDK and agent framework, and the LLM is
always served inside the platform.** You do not need - and would not normally use - the Anthropic
"Claude Agent SDK". This doc records the fact-check (official docs, 2026) so we do not re-litigate it.

## The question

The MVP ladder originally said the faithful rung (now MVP-4) would run a skill "faithfully ... + Claude Agent SDK". That was
wrong for a Databricks-native project. The correction below is verified against official docs.

## Three layers, all Databricks-native

1. **Call the model (what this repo does today).** The job calls a Databricks-served Foundation
   Model / Model Serving endpoint (e.g. `databricks-gpt-oss-120b`, `databricks-claude-opus-4-8`)
   through the **Databricks SDK** - `WorkspaceClient().config.authenticate()` + a REST call, or
   `w.serving_endpoints.query(...)`, or the OpenAI-compatible client. No external API key; the
   model runs inside the workspace security boundary. This is all `src/run_skill.py` needs.

2. **Deploy a queryable, governed agent (not a batch job).** Databricks' own **Mosaic AI Agent
   Framework** (`databricks-agents` SDK):
   - Author the agent against the MLflow **`ResponsesAgent`** interface (the recommended
     production contract - structured input/output, tracing, evaluation).
   - Log + register to Unity Catalog, then `from databricks import agents; agents.deploy(uc_model_name, version)`
     which returns a query endpoint (requires `databricks-agents` >= 0.12.0 to deploy from outside a notebook).
   - Query it with the **Databricks OpenAI client**: `client.responses.create(model=endpoint, input=...)`.
   - Everything governed by Unity Catalog; observability via MLflow.

3. **Agent Bricks / Genie (managed agents).** For no-/low-code managed agents (Knowledge
   Assistant, Supervisor, Genie), Databricks provides Agent Bricks. Agent Bricks offers "all the
   frontier proprietary and open-source models in a single platform, natively integrated into our
   security boundary" - you flex between LLMs without leaving the platform.

## Where the Claude Agent SDK does and does not fit

- It is **not** part of Databricks' agent-invocation story. It is Anthropic's local harness that
  drives Claude Code (Node) and loads `~/.claude/skills/`. Databricks has no dependency on it.
- The only scenario it could appear is replaying a Claude-Code-style **agentic skill loop**
  faithfully (e.g. branded-pptx's write-render-look-fix vision loop), for which Databricks has no
  native "run this SKILL.md loop" runtime (`databricks aitools` installs skills into coding
  assistants but has no `run`). Even then:
  - it would point `ANTHROPIC_BASE_URL` at the **Databricks-served** Claude gateway, so the LLM is
    still inside Databricks - **no vendor intermixing at the model layer**; and
  - the Databricks-native alternative is to build that loop yourself in Python (call the
    Databricks Claude vision endpoint, own the retry logic) or re-cut the skill as a plain job -
    which is exactly the MVP-2 approach. So MVP-4 does not require the Claude Agent SDK.

## Bottom line for this repo

- Keep invoking the LLM via the Databricks SDK (done).
- If we ever need a queryable governed agent, use Mosaic AI Agent Framework (`ResponsesAgent` +
  `agents.deploy` + Databricks OpenAI client), not an Anthropic harness.
- The user's instinct was correct: no cross-vendor SDK is needed; the only cross-vendor surface is
  the LLM API at the lowest level, and Databricks already hides that behind its own platform.

## Sources

- Query an agent deployed on Databricks: https://docs.databricks.com/aws/en/generative-ai/agent-framework/query-agent
- Deploy an agent (Model Serving): https://learn.microsoft.com/en-us/azure/databricks/generative-ai/agent-framework/deploy-agent
- databricks-agents SDK (agents.deploy) API: https://api-docs.databricks.com/python/databricks-agents/latest/databricks_agent_framework.html
- Use agents on Databricks (ResponsesAgent): https://docs.databricks.com/aws/en/generative-ai/agent-framework/build-agents
- Author an AI agent + deploy on Databricks Apps: https://docs.databricks.com/aws/en/generative-ai/agent-framework/author-agent
- Agent Bricks (DAIS 2026): https://www.databricks.com/blog/agent-bricks-dais-2026
- ResponsesAgent (MLflow): https://mlflow.org/docs/latest/genai/serving/responses-agent/
