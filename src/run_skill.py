"""
Job task: RUN an Agent Skill against a document from the INPUT volume and write the
result to the OUTPUT volume.

Unlike MVP-0 (which just called the LLM with a fixed prompt), this loads a real skill
folder and runs both of its halves:
  1. DETERMINISTIC: runs the skill's own scripts/analyze.py -> exact metrics (ground truth).
  2. NON-DETERMINISTIC: calls the inside-Databricks LLM for sentiment / summary / themes,
     grounded in those exact metrics and the document text.
  3. Writes a combined report that LABELS which half is code and which is the LLM.

Watch both halves: set LOG_LEVEL=DEBUG (env) or pass --log-level DEBUG.
Auth is ambient (WorkspaceClient); no secrets in this file.
"""
import argparse
import datetime
import importlib.util
import json
import logging
import os
import re
import time
from pathlib import Path

import requests
from databricks.sdk import WorkspaceClient

log = logging.getLogger("run_skill")

DEFAULT_MODEL = "databricks-gpt-oss-120b"   # free-tier callable; skills may declare their own

# Serving endpoints routinely return 429/5xx while scaling from zero, so a single attempt
# would fail the whole batch on a cold start. Retry only transient failures; a 4xx is a real
# client error and must not be retried.
LLM_MAX_ATTEMPTS = 4                       # 1 try + 3 retries
LLM_RETRY_STATUS = {429, 500, 502, 503, 504}


def load_skill_analyze(skill_dir: Path):
    """Import the skill's deterministic analyze() function by file path."""
    path = skill_dir / "scripts" / "analyze.py"
    spec = importlib.util.spec_from_file_location("skill_analyze", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.analyze


def extract_text(message: dict) -> str:
    """Plain text from a chat message, regardless of model output shape (see MVP-0 notes)."""
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        answer = "\n".join(p.get("text", "") for p in content if p.get("type") != "reasoning")
        if answer.strip():
            return answer.strip()
    return (message.get("reasoning_content") or "").strip() or "(model returned no text)"


def _message_text(payload: dict, model: str) -> str:
    """Pull the assistant text from a serving-endpoint payload, guarding against 200-with-error
    bodies (content-filter blocks, backend faults) that carry an error or no choices - those
    would otherwise crash on choices[0] with an opaque KeyError/IndexError."""
    if isinstance(payload, dict) and payload.get("error_code"):
        raise RuntimeError(f"{model} returned {payload['error_code']}: {payload.get('message', '')}")
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not choices:
        raise RuntimeError(f"{model} returned no choices: {json.dumps(payload)[:200]}")
    return extract_text(choices[0].get("message", {}))


def call_llm(w: WorkspaceClient, model: str, messages: list, max_tokens: int = 800) -> str:
    """POST to an inside-Databricks serving endpoint, with bounded retry on transient errors.

    Retries timeouts, dropped connections, and 429/5xx (endpoint scaling from zero) with
    exponential backoff; a 4xx re-raises immediately as a real client error.
    """
    host = w.config.host.rstrip("/")
    headers = {**w.config.authenticate(), "Content-Type": "application/json"}
    url = f"{host}/serving-endpoints/{model}/invocations"
    last_exc = None
    for attempt in range(1, LLM_MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(
                url, headers=headers,
                json={"messages": messages, "max_tokens": max_tokens},
                timeout=180,
            )
            if resp.status_code not in LLM_RETRY_STATUS:
                resp.raise_for_status()          # 2xx returns below; a non-retryable 4xx raises now
                return _message_text(resp.json(), model)
            last_exc = requests.HTTPError(f"HTTP {resp.status_code} from {model}")
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
        if attempt < LLM_MAX_ATTEMPTS:
            backoff = min(8.0, 0.5 * 2 ** (attempt - 1))
            log.warning("LLM call attempt %d/%d failed (%s); retrying in %.1fs",
                        attempt, LLM_MAX_ATTEMPTS, last_exc, backoff)
            time.sleep(backoff)
    raise last_exc                               # transient errors exhausted all attempts


def _skill_declared_model(skill_md_text: str):
    """Optional per-skill model from the SKILL.md front-matter (a `model:` line, top-level or
    under metadata). Dependency-free regex so the runner keeps its tiny import surface - no
    PyYAML on serverless. Scans only the leading `---` front-matter block, never the body."""
    if not skill_md_text.startswith("---"):
        return None
    end = skill_md_text.find("\n---", 3)
    front_matter = skill_md_text[3:end] if end != -1 else ""
    m = re.search(r"(?m)^\s*model:\s*([^\s#]+)", front_matter)
    return m.group(1).strip().strip("\"'") if m else None


def resolve_model(cli_model, skill_md_text: str):
    """Pick the serving endpoint for this run, most explicit wins. Returns (model, source):

    1. an explicit --model on the CLI/job (cli_model is truthy),
    2. else a `model:` the skill declares in its SKILL.md front-matter (each skill's own choice),
    3. else the built-in DEFAULT_MODEL.
    """
    if cli_model:
        return cli_model, "explicit --model"
    declared = _skill_declared_model(skill_md_text)
    if declared:
        return declared, "SKILL.md front-matter"
    return DEFAULT_MODEL, "built-in default"


MAX_INPUT_CHARS = 200_000  # oversized inputs go to the dead-letter queue, not the model


def write_rejected(rejected_dir: str, in_path: str, source: str, reason: str) -> str:
    """Dead-letter queue: quarantine a bad input instead of failing the whole job.

    Writes the original text plus a .reason.txt sidecar under the rejected volume, so an
    operator can inspect what was rejected and why - like a SQL*Loader reject file or an AWS DLQ.
    Rejecting one bad input never fails the batch; the run still succeeds.
    """
    os.makedirs(rejected_dir, exist_ok=True)
    base = os.path.basename(in_path)
    with open(f"{rejected_dir}/{base}", "w", encoding="utf-8") as f:
        f.write(source)
    with open(f"{rejected_dir}/{base}.reason.txt", "w", encoding="utf-8") as f:
        f.write(reason + "\n")
    return f"{rejected_dir}/{base}"


CONTENT_GUARD_PROMPT = (
    "You are a content guardrail. Inspect the DOCUMENT and respond with ONLY compact JSON: "
    '{"pii": true|false, "unsafe": true|false, "reason": "<= 12 words"}. '
    "pii=true if it contains emails, phone numbers, SSNs, credit-card numbers, or similar personal "
    "identifiers. unsafe=true for hate, violence, self-harm, or clearly malicious intent."
)


def guard_content(w: WorkspaceClient, model: str, text: str):
    """LLM-as-guardrail. Returns (flagged: bool, reason: str).

    Uses the inside-Databricks model to classify the input for PII / unsafe content. Fails OPEN
    (proceeds) if the guard call fails OR its output cannot be parsed, so a guard hiccup - a
    network error as much as garbled JSON - never blocks legitimate content. Production may prefer
    fail-closed, or the platform AI Gateway guardrails (config, not code) - see
    docs/guardrails-and-dead-letter-queue.md. NOTE: an LLM guardrail on raw input is prompt-
    injectable; it is a portable demo control, not a hard boundary (see that doc's Known limits).
    """
    try:
        raw = call_llm(w, model, [
            {"role": "system", "content": CONTENT_GUARD_PROMPT},
            {"role": "user", "content": "DOCUMENT:\n" + text},
        ], max_tokens=400)
        verdict = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
    except Exception as e:  # noqa: BLE001 - guard call failed OR output unparseable -> fail open
        log.warning("content guard unavailable/unparseable, failing open: %s", e)
        return False, ""
    hits = [k for k in ("pii", "unsafe") if verdict.get(k)]
    if hits:
        return True, f"content guardrail flagged {', '.join(hits)}: {verdict.get('reason', '')}".strip()
    return False, ""


def main():
    parser = argparse.ArgumentParser(description="Run an Agent Skill against a document.")
    parser.add_argument("--model", default=None,
                        help="Serving endpoint. If omitted, the skill's own SKILL.md `model:` is "
                             "used, else the built-in default. An explicit value here always wins.")
    parser.add_argument("--skill-dir", default="skills/document-insights",
                        help="Path to the skill folder. The DAB passes the deployed absolute path "
                             "via ${workspace.file_path}; the relative default works for local runs.")
    parser.add_argument("--in-path", default="/Volumes/workspace/genai/input/weekly-update.md")
    parser.add_argument("--out-dir", default="/Volumes/workspace/genai/output")
    parser.add_argument("--rejected-dir", default="/Volumes/workspace/genai/rejected",
                        help="Dead-letter queue: bad/blocked inputs are quarantined here (never fail the batch).")
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    skill_dir = Path(args.skill_dir)
    log.info("running skill at %s", skill_dir)

    with open(args.in_path, "r", encoding="utf-8") as f:
        source = f.read()

    # 0) GUARD: send bad input to the dead-letter queue instead of failing the batch.
    #    (Guardrail-blocked inputs - PII/unsafe - would be quarantined the same way; see
    #    docs/guardrails-and-dead-letter-queue.md.)
    problem = None
    if not source.strip():
        problem = "empty input (no content to analyze)"
    elif len(source) > MAX_INPUT_CHARS:
        problem = f"input too large ({len(source)} chars > {MAX_INPUT_CHARS} limit)"
    if problem:
        dest = write_rejected(args.rejected_dir, args.in_path, source, problem)
        log.warning("REJECTED %s -> %s (%s)", args.in_path, dest, problem)
        print(f"REJECTED {args.in_path} -> {dest} ({problem})")
        return
    log.debug("read %d chars from %s", len(source), args.in_path)

    # Each skill may pick its own model (SKILL.md front-matter); an explicit --model overrides.
    skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    model, model_src = resolve_model(args.model, skill_md)
    log.info("using model %s (%s)", model, model_src)

    w = WorkspaceClient()

    # 0b) CONTENT GUARD (LLM-as-guardrail): quarantine PII / unsafe content to the DLQ.
    #     Platform-native alternative: AI Gateway guardrails as config, not code - see
    #     docs/guardrails-and-dead-letter-queue.md.
    flagged, guard_reason = guard_content(w, model, source)
    if flagged:
        dest = write_rejected(args.rejected_dir, args.in_path, source, guard_reason)
        log.warning("REJECTED %s -> %s (%s)", args.in_path, dest, guard_reason)
        print(f"REJECTED {args.in_path} -> {dest} ({guard_reason})")
        return

    # 1) DETERMINISTIC half - the skill's own code computes exact metrics.
    analyze = load_skill_analyze(skill_dir)
    metrics = analyze(source)
    log.info("deterministic metrics: %s", json.dumps(metrics))

    # 2) NON-DETERMINISTIC half - the LLM interprets, grounded in the exact metrics.
    messages = [
        {"role": "system", "content":
            "Follow the skill instructions exactly. Output clean markdown, no preamble. Use hyphens, not em-dashes."},
        {"role": "user", "content":
            f"SKILL INSTRUCTIONS:\n{skill_md}\n\n"
            f"EXACT METRICS (ground truth - do not recompute):\n{json.dumps(metrics, indent=2)}\n\n"
            f"DOCUMENT:\n{source}\n\n"
            "Produce the interpretive read described by the skill's Output contract above (its audience/"
            "sentiment/coaching/themes half), grounded in the exact metrics and the document. Output ONLY the "
            "body - prose and bullet points, NO markdown headings and NO metrics/scores table (the runner adds "
            "the heading and the table). Reference the exact metrics where natural, but never restate or "
            "recompute a number as if you produced it."},
    ]
    log.debug("calling model %s", args.model)
    # Headroom for reasoning models (e.g. gpt-oss): they spend tokens thinking before the
    # answer, so too small a budget can return empty content (the extract_text sentinel).
    reading = call_llm(w=w, model=model, messages=messages, max_tokens=1500)
    log.info("llm reading produced (%d chars)", len(reading))

    # 3) Combined, clearly-labeled report.
    today = datetime.date.today().isoformat()
    stem = os.path.splitext(os.path.basename(args.in_path))[0]
    skill_name = skill_dir.name
    title = skill_name.replace("-", " ").replace("_", " ").title()
    metrics_rows = "\n".join(f"| {k.replace('_', ' ')} | {v} |" for k, v in metrics.items())
    report = (
        f"# {title} - {today}\n\n"
        f"_Source: `{os.path.basename(args.in_path)}` | skill: `{skill_name}` | "
        f"model: `{model}` (inside Databricks, no external key)._\n\n"
        f"## Metrics (computed by code - exact)\n\n"
        f"| metric | value |\n| --- | --- |\n{metrics_rows}\n\n"
        f"## Reading (interpreted by the LLM)\n\n{reading}\n"
    )
    os.makedirs(args.out_dir, exist_ok=True)
    # Skill-namespaced so two skills over the same input on the same day do not collide
    # (document-insights and readability would otherwise both write <stem>-insights-<date>.md).
    out_path = f"{args.out_dir}/{stem}-{skill_name}-{today}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    log.info("WROTE %s", out_path)
    print(f"READ  {args.in_path}")
    print(f"WROTE {out_path}")


if __name__ == "__main__":
    main()
