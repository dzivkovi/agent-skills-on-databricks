"""Contract test over EVERY skill in skills/. Auto-covers new skills as they are added,
so the multi-skill invariant (each skill is independently runnable by the job) is enforced
for document-insights, readability, and anything published later.
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = sorted(
    p for p in (ROOT / "skills").iterdir()
    if p.is_dir() and (p / "SKILL.md").exists()
)


def _load_analyze(skill_dir: Path):
    spec = importlib.util.spec_from_file_location(
        f"{skill_dir.name}_analyze", skill_dir / "scripts" / "analyze.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.analyze


def test_at_least_two_skills_present():
    # The whole point of #6: prove MULTIPLE independent skills coexist.
    assert len(SKILLS) >= 2, f"expected 2+ skills, found {[s.name for s in SKILLS]}"


def _front_matter(text: str) -> str:
    """The leading ---...--- block only (same boundary logic the runner uses)."""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


@pytest.mark.parametrize("skill_dir", SKILLS, ids=lambda p: p.name)
def test_skill_has_required_shape(skill_dir):
    assert (skill_dir / "SKILL.md").is_file()
    assert (skill_dir / "scripts" / "analyze.py").is_file(), \
        f"{skill_dir.name} missing scripts/analyze.py"
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---"), f"{skill_dir.name} SKILL.md needs YAML front-matter"
    # name:/description: must be IN the front-matter block, not merely somewhere in the body.
    fm = _front_matter(text)
    assert "name:" in fm and "description:" in fm, \
        f"{skill_dir.name} front-matter missing name:/description:"


@pytest.mark.parametrize("skill_dir", SKILLS, ids=lambda p: p.name)
def test_skill_analyze_returns_nonempty_str_keyed_dict(skill_dir):
    analyze = _load_analyze(skill_dir)
    result = analyze("Hello world. This is a test sentence with several plain words.")
    assert isinstance(result, dict) and result, "analyze() must return a non-empty dict"
    assert all(isinstance(k, str) for k in result), "all metric keys must be strings"


@pytest.mark.parametrize("skill_dir", SKILLS, ids=lambda p: p.name)
def test_skill_analyze_handles_empty_input(skill_dir):
    # A skill's deterministic half must not crash on empty text (the runner guards empty
    # inputs, but analyze() is also called standalone).
    result = _load_analyze(skill_dir)("")
    assert isinstance(result, dict)
