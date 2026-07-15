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
from pathlib import Path

import requests
from databricks.sdk import WorkspaceClient

log = logging.getLogger("run_skill")


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


def call_llm(w: WorkspaceClient, model: str, messages: list, max_tokens: int = 800) -> str:
    host = w.config.host.rstrip("/")
    headers = {**w.config.authenticate(), "Content-Type": "application/json"}
    resp = requests.post(
        f"{host}/serving-endpoints/{model}/invocations",
        headers=headers,
        json={"messages": messages, "max_tokens": max_tokens},
        timeout=180,
    )
    resp.raise_for_status()
    return extract_text(resp.json()["choices"][0]["message"])


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
    (proceeds) if the guard's output cannot be parsed, so a guard hiccup never blocks legitimate
    content. Production may prefer fail-closed, or the platform AI Gateway guardrails (config, not
    code) - see docs/guardrails-and-dead-letter-queue.md.
    """
    raw = call_llm(w, model, [
        {"role": "system", "content": CONTENT_GUARD_PROMPT},
        {"role": "user", "content": "DOCUMENT:\n" + text},
    ], max_tokens=400)
    try:
        verdict = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
    except Exception:  # noqa: BLE001 - unparseable guard output -> fail open
        log.warning("content guard output unparseable, failing open: %r", raw[:120])
        return False, ""
    hits = [k for k in ("pii", "unsafe") if verdict.get(k)]
    if hits:
        return True, f"content guardrail flagged {', '.join(hits)}: {verdict.get('reason', '')}".strip()
    return False, ""


def main():
    parser = argparse.ArgumentParser(description="Run an Agent Skill against a document.")
    parser.add_argument("--model", default="databricks-gpt-oss-120b")
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

    w = WorkspaceClient()

    # 0b) CONTENT GUARD (LLM-as-guardrail): quarantine PII / unsafe content to the DLQ.
    #     Platform-native alternative: AI Gateway guardrails as config, not code - see
    #     docs/guardrails-and-dead-letter-queue.md.
    flagged, guard_reason = guard_content(w, args.model, source)
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
    skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    messages = [
        {"role": "system", "content":
            "Follow the skill instructions exactly. Output clean markdown, no preamble. Use hyphens, not em-dashes."},
        {"role": "user", "content":
            f"SKILL INSTRUCTIONS:\n{skill_md}\n\n"
            f"EXACT METRICS (ground truth - do not recompute):\n{json.dumps(metrics, indent=2)}\n\n"
            f"DOCUMENT:\n{source}\n\n"
            "Produce ONLY the interpretive read: overall sentiment (positive/neutral/negative) with a short "
            "justification, a one-sentence summary, and 2-3 key themes as a bulleted list. You may reference the "
            "exact metrics where natural, but never restate a count as if you computed it."},
    ]
    log.debug("calling model %s", args.model)
    reading = call_llm(w=w, model=args.model, messages=messages)
    log.info("llm reading produced (%d chars)", len(reading))

    # 3) Combined, clearly-labeled report.
    today = datetime.date.today().isoformat()
    stem = os.path.splitext(os.path.basename(args.in_path))[0]
    metrics_rows = "\n".join(f"| {k.replace('_', ' ')} | {v} |" for k, v in metrics.items())
    report = (
        f"# Document Insights - {today}\n\n"
        f"_Source: `{os.path.basename(args.in_path)}` | skill: `document-insights` | "
        f"model: `{args.model}` (inside Databricks, no external key)._\n\n"
        f"## Metrics (computed by code - exact)\n\n"
        f"| metric | value |\n| --- | --- |\n{metrics_rows}\n\n"
        f"## Reading (interpreted by the LLM)\n\n{reading}\n"
    )
    os.makedirs(args.out_dir, exist_ok=True)
    out_path = f"{args.out_dir}/{stem}-insights-{today}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    log.info("WROTE %s", out_path)
    print(f"READ  {args.in_path}")
    print(f"WROTE {out_path}")


if __name__ == "__main__":
    main()
