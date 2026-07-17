"""
Creds-free unit tests for the manifest handoff (issue #17): the contract that lets two runs of
the SAME runner (src/run_skill.py) form a chain via --manifest-out / --manifest-in.

These tests cover only write_manifest / read_manifest and the payload shape they carry - never
main() end to end, which needs a live WorkspaceClient. The live counterpart that proves the
DEPLOYED chain (report_to_deck: analyze -> deck) lives in scripts/e2e_chain_test.py.
"""
import json

import pytest

import run_skill


def test_write_then_read_round_trips_an_ok_payload(tmp_path):
    manifest_path = tmp_path / "output" / "_runs" / "run-123" / "manifest.json"
    payload = {"status": "ok", "report_path": "/Volumes/workspace/genai/output/x.md",
               "skill": "document-insights", "model": "databricks-gpt-oss-120b"}

    # The _runs/<run_id>/ directory does not exist yet - write_manifest must create it, the same
    # way write_rejected creates the rejected volume on first use.
    assert not manifest_path.parent.exists()
    run_skill.write_manifest(str(manifest_path), payload)
    assert manifest_path.parent.is_dir()

    assert run_skill.read_manifest(str(manifest_path)) == payload


def test_write_manifest_writes_valid_json_with_exact_keys(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    payload = {"status": "ok", "report_path": "/Volumes/workspace/genai/output/report.md",
               "skill": "document-insights", "model": "databricks-gpt-oss-120b"}
    run_skill.write_manifest(str(manifest_path), payload)

    # Read the raw file directly - not through read_manifest - so this proves what actually
    # landed on disk, not just that the two helpers agree with each other.
    with open(manifest_path, encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk == payload
    assert set(on_disk) == {"status", "report_path", "skill", "model"}


def test_rejected_payload_round_trips_with_status_and_reason(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    payload = {"status": "rejected", "reason": "content guardrail flagged pii: fake SSN"}
    run_skill.write_manifest(str(manifest_path), payload)
    assert run_skill.read_manifest(str(manifest_path)) == payload


def test_read_manifest_missing_file_raises_file_not_found(tmp_path):
    # The job graph promised this file exists (the analyze task's --manifest-out is the deck
    # task's --manifest-in); a downstream task must not quietly invent an input on a miss.
    missing = tmp_path / "_runs" / "no-such-run" / "manifest.json"
    with pytest.raises(FileNotFoundError):
        run_skill.read_manifest(str(missing))


def test_read_manifest_malformed_json_raises_json_decode_error(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        run_skill.read_manifest(str(manifest_path))
