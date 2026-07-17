"""
Skill entrypoint for branded-pptx, per the uniform skill contract (issue #16).

Unlike the analyze()+LLM skills, branded-pptx is a pure builder: the deck is a deterministic
function of the markdown (see sibling scripts/build_pptx.py), so this shim never touches
ctx["llm"]. The only per-skill choice is which brand skin to apply, read from an optional
`brand:` line in the skill's own SKILL.md front-matter.

Must stay standalone: published skills live alone on a UC volume with no repo on the
Python path, so this file loads build_pptx.py by file path rather than importing it.
python-pptx must be a runtime dependency of the job (databricks.yml) for this to run on
Databricks - wiring tracked in issue #2.
"""
import importlib.util
import io
import re
from pathlib import Path

DEFAULT_BRAND = "default"


def _load_build_pptx(skill_dir: Path):
    """Import the skill's deterministic build_deck() function by file path (no repo import)."""
    path = skill_dir / "scripts" / "build_pptx.py"
    spec = importlib.util.spec_from_file_location("branded-pptx_build_pptx", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_deck


def _skill_declared_brand(skill_md_text: str) -> str:
    """Optional per-skill brand from the SKILL.md front-matter (a `brand:` line, top-level or
    under metadata). Same dependency-free regex approach the runner uses for `model:` - no
    PyYAML on serverless. Scans only the leading `---` front-matter block, never the body."""
    skill_md_text = skill_md_text.lstrip("﻿")   # tolerate a UTF-8 BOM before the fence
    if not skill_md_text.startswith("---"):
        return DEFAULT_BRAND
    end = skill_md_text.find("\n---", 3)
    front_matter = skill_md_text[3:end] if end != -1 else ""
    m = re.search(r"(?m)^\s*brand:\s*([^\s#]+)", front_matter)
    return m.group(1).strip().strip("\"'") if m else DEFAULT_BRAND


def run(ctx: dict) -> str:
    """Build the deck deterministically and write it. Returns the path written."""
    skill_dir = Path(ctx["skill_dir"])
    build_deck = _load_build_pptx(skill_dir)
    brand = _skill_declared_brand(ctx["skill_md"])

    # Build in memory, then write the finished bytes in ONE sequential write. A .pptx is a zip,
    # and zipfile seeks while packing; a Unity Catalog volume does not support random-access
    # writes, so saving straight to /Volumes/... dies at ZipFile.close() with OSError errno 5
    # (I/O error) or 95 (operation not supported). Sequential writes are fine - that is why the
    # markdown reports work. BytesIO gives zipfile the seekable target it needs.
    out = ctx["out_base"] + ".pptx"
    buf = io.BytesIO()
    build_deck(ctx["text"], buf, brand=brand)
    with open(out, "wb") as f:
        f.write(buf.getvalue())
    ctx["log"].info("built deck %s (brand=%s, %d bytes)", out, brand, buf.getbuffer().nbytes)
    return out
