"""
Skill entrypoint for document-insights, per the uniform skill contract (issue #16).

The job runner (src/run_skill.py) no longer knows this skill's report shape - it only
calls run(ctx) -> output_path. This shim pairs the deterministic half (sibling
scripts/analyze.py, exact word/character/sentence metrics) with the LLM half (the runner's
retrying inside-Databricks client, grounded in those exact metrics), then writes the same
combined report byte-for-byte as the pre-#16 runner did.

Must stay standalone: published skills live alone on a UC volume with no repo on the
Python path, so this file loads analyze.py by file path rather than importing it.
"""
import importlib.util
import json
import os
from pathlib import Path


def _load_analyze(skill_dir: Path, skill_name: str):
    """Import the skill's deterministic analyze() function by file path (no repo import)."""
    path = skill_dir / "scripts" / "analyze.py"
    spec = importlib.util.spec_from_file_location(f"{skill_name}_analyze", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.analyze


def run(ctx: dict) -> str:
    """Run both halves of the skill and write the combined report. Returns the path written."""
    skill_dir = Path(ctx["skill_dir"])
    analyze = _load_analyze(skill_dir, ctx["skill_name"])

    # 1) DETERMINISTIC half - the skill's own code computes exact metrics.
    metrics = analyze(ctx["text"])
    ctx["log"].info("deterministic metrics: %s", json.dumps(metrics))

    # 2) NON-DETERMINISTIC half - the LLM interprets, grounded in the exact metrics.
    messages = [
        {"role": "system", "content":
            "Follow the skill instructions exactly. Output clean markdown, no preamble. Use hyphens, not em-dashes."},
        {"role": "user", "content":
            f"SKILL INSTRUCTIONS:\n{ctx['skill_md']}\n\n"
            f"EXACT METRICS (ground truth - do not recompute):\n{json.dumps(metrics, indent=2)}\n\n"
            f"DOCUMENT:\n{ctx['text']}\n\n"
            "Produce the interpretive read described by the skill's Output contract above (its audience/"
            "sentiment/coaching/themes half), grounded in the exact metrics and the document. Output ONLY the "
            "body - prose and bullet points, NO markdown headings and NO metrics/scores table (the runner adds "
            "the heading and the table). Reference the exact metrics where natural, but never restate or "
            "recompute a number as if you produced it."},
    ]
    reading = ctx["llm"](messages, max_tokens=1500)
    ctx["log"].info("llm reading produced (%d chars)", len(reading))

    # 3) Combined, clearly-labeled report - identical shape to the pre-#16 runner's report.
    skill_name = ctx["skill_name"]
    title = skill_name.replace("-", " ").replace("_", " ").title()
    metrics_rows = "\n".join(f"| {k.replace('_', ' ')} | {v} |" for k, v in metrics.items())
    report = (
        f"# {title} - {ctx['today']}\n\n"
        f"_Source: `{os.path.basename(ctx['in_path'])}` | skill: `{skill_name}` | "
        f"model: `{ctx['model']}` (inside Databricks, no external key)._\n\n"
        f"## Metrics (computed by code - exact)\n\n"
        f"| metric | value |\n| --- | --- |\n{metrics_rows}\n\n"
        f"## Reading (interpreted by the LLM)\n\n{reading}\n"
    )
    out_path = ctx["out_base"] + ".md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    return out_path
