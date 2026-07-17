"""Contract test over EVERY skill in skills/. Auto-covers new skills as they are added.

Skills come in more than one shape: an "analyze" skill (document-insights, readability) exposes
scripts/analyze.py:analyze(text)->dict and is run by src/run_skill.py; a "builder" skill
(branded-pptx) exposes a different entrypoint (scripts/build_pptx.py) and produces an artifact.
So the UNIVERSAL contract every skill must meet is SKILL.md front-matter + at least one script;
the analyze()->dict contract is asserted only for skills that actually ship scripts/analyze.py.
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = sorted(
    p for p in (ROOT / "skills").iterdir()
    if p.is_dir() and (p / "SKILL.md").exists()
)
ANALYZE_SKILLS = [p for p in SKILLS if (p / "scripts" / "analyze.py").exists()]


def _load_analyze(skill_dir: Path):
    spec = importlib.util.spec_from_file_location(
        f"{skill_dir.name}_analyze", skill_dir / "scripts" / "analyze.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.analyze


def _front_matter(text: str) -> str:
    """The leading ---...--- block only (same boundary logic the runner uses)."""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


def test_at_least_two_skills_present():
    # The whole point of #6: prove MULTIPLE independent skills coexist.
    assert len(SKILLS) >= 2, f"expected 2+ skills, found {[s.name for s in SKILLS]}"


@pytest.mark.parametrize("skill_dir", SKILLS, ids=lambda p: p.name)
def test_skill_has_required_shape(skill_dir):
    # Universal contract: SKILL.md front-matter with name:/description:, and at least one script.
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---"), f"{skill_dir.name} SKILL.md needs YAML front-matter"
    fm = _front_matter(text)
    assert "name:" in fm and "description:" in fm, \
        f"{skill_dir.name} front-matter missing name:/description:"
    scripts = list((skill_dir / "scripts").glob("*.py")) if (skill_dir / "scripts").is_dir() else []
    assert scripts, f"{skill_dir.name} has no scripts/*.py entrypoint"
    assert not (skill_dir / "scripts" / "run.py").exists(), \
        f"{skill_dir.name} ships scripts/run.py - an imported skill must stay read-only, harness " \
        "code belongs in src/adapters.py, not inside the skill"


@pytest.mark.parametrize("skill_dir", ANALYZE_SKILLS, ids=lambda p: p.name)
def test_analyze_skill_returns_nonempty_str_keyed_dict(skill_dir):
    analyze = _load_analyze(skill_dir)
    result = analyze("Hello world. This is a test sentence with several plain words.")
    assert isinstance(result, dict) and result, "analyze() must return a non-empty dict"
    assert all(isinstance(k, str) for k in result), "all metric keys must be strings"


@pytest.mark.parametrize("skill_dir", ANALYZE_SKILLS, ids=lambda p: p.name)
def test_analyze_skill_handles_empty_input(skill_dir):
    # An analyze skill's deterministic half must not crash on empty text (the runner guards empty
    # inputs, but analyze() is also called standalone).
    result = _load_analyze(skill_dir)("")
    assert isinstance(result, dict)
