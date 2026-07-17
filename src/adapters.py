"""
The Databricks-specific half of running a skill. THIS is where platform knowledge lives.

The boundary this file exists to hold: an imported skill is READ-ONLY. You bring the same
folder Claude Code uses (SKILL.md plus its own scripts/) and change nothing about it. Anything
Databricks needs - how to shape the output, how to write it to a Unity Catalog volume, how to
call the inside-Databricks model - is an ADAPTER, and adapters live here in the harness.

An adapter is run(ctx) -> the path it wrote. The job picks one with --adapter; skills never
declare it, because "which adapter" is a fact about the platform, not about the skill.

  report (default) - a skill whose deterministic half is scripts/analyze.py returning a dict of
                     exact metrics. Pairs it with an LLM reading and writes a labeled markdown
                     report. Both document-insights and readability use this, unmodified.
  deck             - a skill whose deterministic half is scripts/build_pptx.py. Writes a real
                     .pptx. Carries the volume workaround (see below).

Adding a third skill SHAPE means adding an adapter here. Adding a third skill of an existing
shape means adding nothing at all - publish the folder and point --skill-dir at it.
"""
import importlib.util
import io
import json
import os
from pathlib import Path


def _load(skill_dir: str, module_file: str, attr: str):
    """Load one function out of a skill's own script, by file path.

    By path, never by import: a published skill sits alone on a UC volume with no repo and no
    package around it, exactly as it sat in ~/.claude/skills. That is the property we are
    protecting - the folder is portable because nothing was done to it.
    """
    path = Path(skill_dir) / "scripts" / module_file
    if not path.is_file():
        raise SystemExit(
            f"skill at {skill_dir} has no scripts/{module_file}, which the "
            f"'{attr}' adapter needs. Wrong --adapter for this skill?")
    spec = importlib.util.spec_from_file_location(f"skill_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, attr)


def report(ctx: dict) -> str:
    """Exact metrics from the skill's code + an interpretive read from the LLM, labeled so a
    reader can see the seam. The two halves stay separated on purpose: an LLM guess must never
    masquerade as a counted fact."""
    analyze = _load(ctx["skill_dir"], "analyze.py", "analyze")

    # 1) DETERMINISTIC half - the skill's own code computes exact metrics.
    metrics = analyze(ctx["text"])
    ctx["log"].info("deterministic metrics: %s", json.dumps(metrics))

    # 2) NON-DETERMINISTIC half - the LLM interprets, grounded in those exact metrics. SKILL.md
    #    is the prompt: the skill's own Output contract decides what the reading contains, which
    #    is why two different skills produce two different readings through this one adapter.
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

    # 3) Combined, clearly-labeled report.
    skill_name = ctx["skill_name"]
    title = skill_name.replace("-", " ").replace("_", " ").title()
    metrics_rows = "\n".join(f"| {k.replace('_', ' ')} | {v} |" for k, v in metrics.items())
    out_path = ctx["out_base"] + ".md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(
            f"# {title} - {ctx['today']}\n\n"
            f"_Source: `{os.path.basename(ctx['in_path'])}` | skill: `{skill_name}` | "
            f"model: `{ctx['model']}` (inside Databricks, no external key)._\n\n"
            f"## Metrics (computed by code - exact)\n\n"
            f"| metric | value |\n| --- | --- |\n{metrics_rows}\n\n"
            f"## Reading (interpreted by the LLM)\n\n{reading}\n"
        )
    return out_path


def deck(ctx: dict) -> str:
    """A real .pptx from the skill's deterministic builder. No LLM: the deck is a pure function
    of the markdown, which is what makes it testable."""
    build_deck = _load(ctx["skill_dir"], "build_pptx.py", "build_deck")

    # THE DATABRICKS TAX, and why it belongs here and not in the skill: a .pptx is a zip, and
    # zipfile SEEKS while packing. A Unity Catalog volume only supports sequential writes, so
    # saving straight to /Volumes/... dies at ZipFile.close() with OSError errno 5 (I/O error)
    # or 95 (operation not supported). Markdown never hit this because it is one write. Build in
    # memory, then write the finished bytes once. The same applies to .docx and .xlsx - every
    # Office format is a zip. The skill's builder needed no patch: python-pptx already accepts a
    # file-like object, so the workaround is entirely the harness's.
    out = ctx["out_base"] + ".pptx"
    buf = io.BytesIO()
    build_deck(ctx["text"], buf)          # the skill's own brand default; its CLI takes --brand
    with open(out, "wb") as f:
        f.write(buf.getvalue())
    ctx["log"].info("built deck %s (%d bytes)", out, buf.getbuffer().nbytes)
    return out


ADAPTERS = {"report": report, "deck": deck}
