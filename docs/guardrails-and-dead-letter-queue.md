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
- A **content guardrail** is wired in: [`src/run_skill.py`](../src/run_skill.py) asks the
  inside-Databricks model to classify each input for PII / unsafe content (LLM-as-guardrail) and
  quarantines flagged inputs to the same reject queue. It fails OPEN on an unparseable verdict, so a
  guard hiccup never blocks legitimate content. (The platform-native alternative is the AI Gateway
  guardrails config in Part 1; the LLM classifier is the portable, free-tier path.)

Verified by [`scripts/reject_test.py`](../scripts/reject_test.py): both a whitespace-only input
(reason "empty input") AND a document with PII (reason "content guardrail flagged pii: ...") are
quarantined in `rejected/` with a `.reason.txt`, the job returns `SUCCESS`, and no output is
produced - while a benign document still passes through (no false reject).

**Testing guardrails safely in a public repo:** the PII case uses obviously-fake,
reserved-for-testing values (email `@example.com`, SSN `123-45-6789`, the Visa test card
`4111 1111 1111 1111`, the `555-01xx` fictional phone range). No profanity or unsafe content is ever
committed - PII detection is the enterprise guardrail, and it tests cleanly and inoffensively.

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
