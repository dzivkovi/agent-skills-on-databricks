"""#6 acceptance: two skills published INDEPENDENTLY to the shared volume and consumed
INDEPENDENTLY by the job, with updating one skill not touching the other.

Creds-free: the shared UC volume is simulated by a fake WorkspaceClient whose files.upload
writes to a temp dir, so this proves the publish + selection mechanics without a live workspace.
The live counterpart runs in scripts/e2e_test.py against the deployed job.
"""
from pathlib import Path

import publish_skill
import run_skill

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ("The team shipped the release this week. Morale is high and we unblocked two "
          "customers. Latency dropped considerably after the caching change.")


def _analyze_for(name):
    """Exactly how the job selects a skill: load_skill_analyze(--skill-dir)."""
    return run_skill.load_skill_analyze(ROOT / "skills" / name)


def test_each_skill_can_pick_its_own_model():
    # #6 corollary: a skill declares its model in SKILL.md; an explicit --model always wins.
    fm_with = "---\nname: x\nmetadata:\n  model: databricks-claude-opus-4-8\n---\nbody\n"
    fm_none = "---\nname: y\nmetadata:\n  version: 0.1.0\n---\nbody\n"
    assert run_skill.resolve_model("cli-endpoint", fm_with)[0] == "cli-endpoint"       # CLI wins
    assert run_skill.resolve_model(None, fm_with)[0] == "databricks-claude-opus-4-8"   # skill picks
    assert run_skill.resolve_model(None, fm_none) == (run_skill.DEFAULT_MODEL, "built-in default")


def test_readability_skill_declares_its_model():
    # The shipped readability skill demonstrates the feature (document-insights uses the default).
    rb_md = (ROOT / "skills" / "readability" / "SKILL.md").read_text(encoding="utf-8")
    di_md = (ROOT / "skills" / "document-insights" / "SKILL.md").read_text(encoding="utf-8")
    assert run_skill._skill_declared_model(rb_md) == "databricks-gpt-oss-120b"
    assert run_skill._skill_declared_model(di_md) is None


def test_job_selects_skill_by_dir_and_gets_distinct_behavior():
    di = _analyze_for("document-insights")(SAMPLE)
    rb = _analyze_for("readability")(SAMPLE)
    # Selecting a different --skill-dir yields a different deterministic contract.
    assert di != rb
    assert set(di) != set(rb), "the two skills must expose distinct metric contracts"
    assert "word_count" in di, "document-insights should expose word_count"
    assert any("flesch" in k for k in rb), f"readability should expose a flesch score, got {list(rb)}"


# --- Fake UC volume: files.upload mirrors the /Volumes/... path under a temp root -------------
class _FakeFiles:
    def __init__(self, root: Path):
        self.root = root

    def upload(self, dest: str, fh, overwrite: bool = True):
        out = self.root / dest.lstrip("/")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(fh.read())


class _FakeW:
    def __init__(self, root: Path):
        self.files = _FakeFiles(root)


def _published_files(volume: Path, dest_root: str):
    base = volume / dest_root.lstrip("/")
    return {p.relative_to(base): p.read_bytes() for p in base.rglob("*") if p.is_file()}


def test_two_skills_publish_independently_and_update_is_isolated(tmp_path):
    volume = tmp_path / "vol"
    w = _FakeW(volume)
    cat, schema = "workspace", "genai"
    di_root = f"/Volumes/{cat}/{schema}/skills/document-insights"
    rb_root = f"/Volumes/{cat}/{schema}/skills/readability"

    n_di = publish_skill.upload_skill_folder(w, ROOT / "skills" / "document-insights", di_root)
    n_rb = publish_skill.upload_skill_folder(w, ROOT / "skills" / "readability", rb_root)
    assert n_di > 0 and n_rb > 0

    di_files = _published_files(volume, di_root)
    rb_files = _published_files(volume, rb_root)
    assert di_files and rb_files
    # Each skill landed under its own dest root; SKILL.md present in both.
    assert Path("SKILL.md") in di_files and Path("SKILL.md") in rb_files

    # Snapshot readability's published bytes, then update + republish ONLY document-insights.
    rb_snapshot = _published_files(volume, rb_root)
    publish_skill.upload_skill_folder(w, ROOT / "skills" / "document-insights", di_root)
    assert _published_files(volume, rb_root) == rb_snapshot, \
        "republishing one skill must not touch another skill's published files"


def test_upload_skill_folder_skips_pyc_and_pycache(tmp_path):
    # __pycache__/*.pyc must never reach the shared volume (they do on a local run).
    skill = tmp_path / "toy"
    (skill / "scripts" / "__pycache__").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: toy\ndescription: x\n---\n", encoding="utf-8")
    (skill / "scripts" / "analyze.py").write_text("def analyze(t):\n    return {'n': len(t)}\n", encoding="utf-8")
    (skill / "scripts" / "__pycache__" / "analyze.cpython-312.pyc").write_bytes(b"\x00\x01")

    volume = tmp_path / "vol"
    w = _FakeW(volume)
    dest = "/Volumes/workspace/genai/skills/toy"
    publish_skill.upload_skill_folder(w, skill, dest)
    uploaded = _published_files(volume, dest)
    assert Path("SKILL.md") in uploaded
    assert not any("__pycache__" in str(p) or str(p).endswith(".pyc") for p in uploaded)
