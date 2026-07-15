# Guardrails and a dead-letter queue (enterprise quality, mostly no code)

Two things enterprise reviewers ask about that a hello-world usually ignores: **guardrails**
(reject/mask malicious or sensitive content) and a **reject queue** (bad inputs go somewhere
inspectable instead of crashing the batch). On Databricks, most of this is platform config, not
code you write.

## Part 1 - Guardrails: a platform feature, not code you write

Databricks provides **AI Gateway guardrails** as configuration on model serving endpoints. You do
not implement PII detection or a toxicity classifier; you turn on a checkbox / config block.

Guardrail types (input and/or output):

- **PII redaction** - detects PII and replaces it with placeholders before the model call.
- **PII blocking** - rejects a request/response that contains PII.
- **Unsafe content** - blocks hate, violence, self-harm, etc.
- **Jailbreak** - blocks attempts to bypass the model's safety constraints.

### The counter-intuitive fact (good for us)

AI Gateway guardrails are supported on **pay-per-token Foundation Model API endpoints** (the
`databricks-*` endpoints this repo already calls), provisioned-throughput endpoints, and external
model endpoints - **but NOT on custom model serving endpoints** you build yourself. So the exact
setup this starter uses is the *supported* one.

Enabling is config, e.g. an `ai_gateway` block on the serving endpoint (shape, per the docs):

```jsonc
"ai_gateway": {
  "guardrails": {
    "input":  { "pii": { "behavior": "BLOCK" }, "safety": true },
    "output": { "pii": { "behavior": "MASK"  }, "safety": true }
  }
}
```

### No-code options you can use in a batch pipeline today

Even without touching endpoint config, Databricks ships **AI Functions** that are one call each and
run on the Foundation Model APIs:

- **`ai_mask(text, [entities])`** - redacts PII (emails, names, ...) in text. One function, no model
  wiring. Ideal to sanitize an input document before analysis, or an output before delivery.
- **`ai_query(endpoint, prompt, ...)`** - general model call that (with Unity AI Gateway enabled) is
  routed through the gateway, giving governance + usage tracking for batch inference.

### Honest caveat (being verified)

Guardrails are a documented capability of the AI Gateway on pay-per-token endpoints. Whether you
attach *your own* guardrail policy directly to the **shared** `databricks-*` endpoint, versus
front it with an endpoint you control, versus using `ai_mask`/`ai_classify` in the pipeline, is the
one mechanic to confirm for your workspace. When in doubt, `ai_mask` in the pipeline is the
guaranteed, no-config path.

### Sources

- AI governance with Unity AI Gateway: https://docs.databricks.com/aws/en/ai-gateway/
- Configure AI Gateway on model serving endpoints: https://docs.databricks.com/aws/en/ai-gateway/configure-ai-gateway-endpoints
- Guardrails for AI Gateway endpoints: https://docs.databricks.com/aws/en/ai-gateway/guardrails
- `ai_mask` (PII masking): https://docs.databricks.com/aws/en/sql/language-manual/functions/ai_mask
- `ai_query`: https://docs.databricks.com/aws/en/sql/language-manual/functions/ai_query

## Part 2 - Dead-letter queue (implemented in this repo)

In batch processing you have input and output, but some inputs must be **rejected** - malformed,
oversized, empty, or (with guardrails) unsafe. Failing the whole job on one bad record is wrong.
The classic pattern is a reject queue: a SQL\*Loader reject file, an AWS SQS dead-letter queue. We
do the same with a third Unity Catalog volume.

### What this repo does

- A **`rejected` volume** (`/Volumes/<catalog>/<schema>/rejected`) sits beside `input` and `output`.
- [`src/run_skill.py`](../src/run_skill.py) validates the input first. If it is empty or larger than
  `MAX_INPUT_CHARS`, it writes the original text plus a `<name>.reason.txt` sidecar to the rejected
  volume and returns - **the run still succeeds**. One bad input never fails the batch.
- A guardrail-blocked input (PII/unsafe) would be quarantined the exact same way once guardrails are
  wired - the reject queue is the shared destination.

Verified behavior (tested): a whitespace-only input -> job `SUCCESS`, file quarantined in
`rejected/` with reason "empty input (no content to analyze)", and no output produced.

```text
  input volume  ->  run_skill.py  --(valid)-->   output volume   (deliverable)
                         |
                         +-------- (bad/blocked) -> rejected volume + .reason.txt   (dead-letter)
```

### When to graduate

For file-drop batch jobs this volume-based reject queue is enough and stays simple. At larger scale
or in a declarative pipeline, use a Delta **quarantine table** or **Lakeflow pipeline expectations**
(`expect_or_drop` / `expect_or_quarantine`) so rejects are queryable and tracked as data quality
metrics rather than files. That is a deliberate step up, not needed for this starter.
