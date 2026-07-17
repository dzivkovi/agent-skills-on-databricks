"""
End-to-end integration test for THE CHAIN (issue #17): two runs of the SAME runner, wired
job-graph -> analyze -> deck, via the --manifest-out / --manifest-in handoff.

The counterpart to e2e_pptx_test.py (single builder task) and e2e_reject_test.py (single-task
dead-letter queue). This is the one #17's acceptance turns on, because a chain has a failure
mode neither sibling can exercise: what happens to the SECOND task when the FIRST one rejects
its input. Two cases, both against the deployed report_to_deck job:

    happy  -> a benign document flows all the way through: analyze writes a report, its
              manifest says status=ok, deck reads that manifest and builds a real .pptx.
    reject -> a document with obvious fake PII gets quarantined by analyze's content guard;
              its manifest says status=rejected; deck reads that, SKIPS, and the job still
              SUCCEEDS - the reject-queue promise (one bad input never fails the batch) held
              across an entire chain, not just one task.

Reopening the deck with python-pptx (not just checking its name) is mandatory - a name check
would pass on a corrupt file, which is the bug that shipped in #2.

Exit code 0 = PASS, 1 = FAIL. Run after `databricks bundle deploy`:

    python scripts/e2e_chain_test.py --profile coldstart
"""
import argparse
import datetime
import io
import json
import os
import subprocess
import sys
import time

from databricks.sdk import WorkspaceClient
from pptx import Presentation

# H1 + two H2 sections + bullets - the shape branded-pptx maps to a title slide plus one slide
# per H2. Deliberately NO numeric run token in the TEXT: the content guard reads the DOCUMENT
# only, and a token like 20260716-203317 can read as an identifier and get quarantined - a flaky
# suite that misdiagnoses as a builder bug (the exact bug caught in #2). The token lives in the
# FILENAME instead, which the guard never sees.
HAPPY_DOC = """# Chain Status Update

The report-to-deck chain turns one markdown document into a report and a deck automatically.

## What changed

- One run of the skill runner writes a manifest for the next run to read
- A rejected upstream input makes the downstream task skip instead of failing

## What is next

- Point the chain at a real weekly document on a schedule
"""

# Reuses the exact fake-PII style from e2e_reject_test.py's "pii" case: email @example.com, a
# reserved SSN, the Visa test card number, and the 555-01xx fictional phone range - all safe to
# commit, and all obviously things the content guard SHOULD flag.
REJECT_DOC = (
    b"Please contact Jane Doe at jane.doe@example.com or 555-0123. "
    b"Her SSN is 123-45-6789 and test card 4111 1111 1111 1111."
)


def resolve_job_id(profile: str, resource_key: str) -> int:
    out = subprocess.run(
        ["databricks", "bundle", "summary", "-o", "json", "-p", profile],
        capture_output=True, text=True, check=True,
    ).stdout
    jobs = json.loads(out).get("resources", {}).get("jobs", {})
    if resource_key not in jobs:
        raise SystemExit(f"FAIL: job resource '{resource_key}' not found in bundle summary. "
                         f"Did you run `databricks bundle deploy -p {profile}` from this folder?")
    return int(jobs[resource_key]["id"])


def _rm(w: WorkspaceClient, path):
    """Best-effort delete - quiet on purpose for paths that are expected to be absent
    (e.g. no deck in the reject case, no rejected pair in the happy case)."""
    if not path:
        return
    try:
        w.files.delete(path)
    except Exception:  # noqa: BLE001 - cleanup is best-effort
        pass


def case_happy(w, job_id, base, timeout_min, keep, started):
    def step(msg):
        print(f"[chain +{time.time()-started:5.1f}s] {msg}")

    out_dir = f"{base}/output"
    token = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = f"chain-happy-{token}"
    in_path = f"{base}/input/{stem}.md"

    step(f"CASE 'happy': upload benign document -> {in_path}")
    w.files.upload(in_path, io.BytesIO(HAPPY_DOC.encode("utf-8")), overwrite=True)

    report_path = None
    report_stem = None
    deck_path = None
    manifest_path = None
    try:
        step("  triggering job (job_parameters) and waiting for terminal state...")
        run = w.jobs.run_now(job_id=job_id, job_parameters={"in_path": in_path}).result(
            timeout=datetime.timedelta(minutes=timeout_min))
        state = run.state.result_state.value if run.state and run.state.result_state else "UNKNOWN"
        manifest_path = f"{out_dir}/_runs/{run.run_id}/manifest.json"
        step(f"  run finished: result_state={state}  url={run.run_page_url}")

        # The analyze task's report - found by the stem-plus-skill prefix (no date guess: the
        # job's clock, not this machine's, decides the date, so recomputing it here would be
        # fragile near a UTC midnight boundary).
        reports = [e.path for e in w.files.list_directory_contents(out_dir)
                   if e.path.rsplit("/", 1)[-1].startswith(f"{stem}-document-insights")
                   and e.path.endswith(".md")]
        report_path = reports[0] if len(reports) == 1 else None
        if report_path:
            report_stem = os.path.basename(report_path)[:-len(".md")]

        # The deck task's output - namespaced off the REPORT's own name (its in_path came from
        # the manifest, not from --in-path), per the runner's output_base contract.
        decks = []
        if report_stem:
            decks = [e.path for e in w.files.list_directory_contents(out_dir)
                     if e.path.rsplit("/", 1)[-1].startswith(f"{report_stem}-branded-pptx")
                     and e.path.endswith(".pptx")]
        deck_path = decks[0] if len(decks) == 1 else None

        # Name the cause when the report exists but no deck does: the deck task re-guards its
        # input, so it can quarantine the REPORT. Without this the failure reads as a builder
        # bug, which is the misdiagnosis the sibling pptx suite already guards against.
        if report_stem and not deck_path:
            quarantined = [e.path for e in w.files.list_directory_contents(f"{base}/rejected")
                           if e.path.rsplit("/", 1)[-1].startswith(report_stem)]
            if quarantined:
                step(f"  NOTE: the deck task's content guard quarantined the REPORT "
                     f"({quarantined}); the skill never ran.")

        prs_ok = False
        slide_count = 0
        if deck_path:
            data = w.files.download(deck_path).contents.read()
            step(f"  downloaded {deck_path} ({len(data)} bytes)")
            # Reopening is the point - a name check would pass on a corrupt file (the #2 bug).
            prs = Presentation(io.BytesIO(data))
            slide_count = len(prs.slides)
            prs_ok = True

        checks = {
            "job result_state == SUCCESS": state == "SUCCESS",
            "analyze wrote a report": report_path is not None,
            "deck was built for that report": deck_path is not None,
            "deck reopens with python-pptx": prs_ok,
            "deck has >= 2 slides": slide_count >= 2,
        }
        for name, ok in checks.items():
            step(f"    assert {name}: {'ok' if ok else 'FAILED'}")
        ok = all(checks.values())
        step(f"  -> {'PASS' if ok else 'FAIL'}")
        return ok
    finally:
        if not keep:
            for p in (in_path, report_path, deck_path, manifest_path):
                _rm(w, p)
            # Normally absent on the happy path; present only if a guard misfired. Two possible
            # names: the input (quarantined by analyze) or the REPORT (quarantined by deck, which
            # re-guards its own input) - write_rejected names the file after whatever it rejected.
            for name in (stem, report_stem):
                if not name:
                    continue
                _rm(w, f"{base}/rejected/{name}.md")
                _rm(w, f"{base}/rejected/{name}.md.reason.txt")
            step("  cleaned up 'happy' test files")


def case_reject(w, job_id, base, timeout_min, keep, started):
    def step(msg):
        print(f"[chain +{time.time()-started:5.1f}s] {msg}")

    out_dir = f"{base}/output"
    rejected_dir = f"{base}/rejected"
    token = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = f"chain-reject-{token}"
    name = f"{stem}.md"
    in_path = f"{base}/input/{name}"

    step(f"CASE 'reject': upload fake-PII document -> {in_path}")
    w.files.upload(in_path, io.BytesIO(REJECT_DOC), overwrite=True)

    manifest_path = None
    try:
        step("  triggering job (job_parameters) and waiting for terminal state...")
        run = w.jobs.run_now(job_id=job_id, job_parameters={"in_path": in_path}).result(
            timeout=datetime.timedelta(minutes=timeout_min))
        state = run.state.result_state.value if run.state and run.state.result_state else "UNKNOWN"
        manifest_path = f"{out_dir}/_runs/{run.run_id}/manifest.json"
        step(f"  run finished: result_state={state}  url={run.run_page_url}")

        # No deck anywhere in the output for this input - the deciding negative assertion.
        no_deck = not any(e.path.rsplit("/", 1)[-1].startswith(stem)
                           and e.path.endswith(".pptx")
                           for e in w.files.list_directory_contents(out_dir))

        rejected = [e.path for e in w.files.list_directory_contents(rejected_dir)]
        quarantined = any(p.endswith(name) for p in rejected)
        reason_ok = any(p.endswith(name + ".reason.txt") for p in rejected)

        # THE DECIDING ASSERTION: the whole JOB still succeeds. A quarantined input produces no
        # report, so deck's --manifest-in reads status=rejected, SKIPS, and returns exit 0 - the
        # reject-queue promise (one bad input never fails the batch) held across the chain.
        checks = {
            "whole job result_state == SUCCESS": state == "SUCCESS",
            "no deck produced for this input": no_deck,
            "input quarantined into rejected volume": quarantined,
            "reason.txt sidecar written": reason_ok,
        }
        for name_, ok in checks.items():
            step(f"    assert {name_}: {'ok' if ok else 'FAILED'}")
        ok = all(checks.values())
        step(f"  -> {'PASS' if ok else 'FAIL'}")
        return ok
    finally:
        if not keep:
            _rm(w, in_path)
            _rm(w, f"{rejected_dir}/{name}")
            _rm(w, f"{rejected_dir}/{name}.reason.txt")
            _rm(w, manifest_path)
            step("  cleaned up 'reject' test files")


def main():
    ap = argparse.ArgumentParser(description="Black-box e2e test of the report_to_deck chain.")
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE", "coldstart"))
    ap.add_argument("--catalog", default=os.environ.get("DATABRICKS_CATALOG", "workspace"))
    ap.add_argument("--schema", default=os.environ.get("DATABRICKS_SCHEMA", "genai"))
    ap.add_argument("--resource-key", default="report_to_deck")
    ap.add_argument("--timeout-min", type=int, default=10)
    ap.add_argument("--keep", action="store_true", help="Do not delete the test files afterward.")
    args = ap.parse_args()

    w = WorkspaceClient(profile=args.profile)
    job_id = resolve_job_id(args.profile, args.resource_key)
    base = f"/Volumes/{args.catalog}/{args.schema}"
    started = time.time()
    print(f"[chain +{time.time()-started:5.1f}s] job_id={job_id}  base={base}")

    happy_ok = case_happy(w, job_id, base, args.timeout_min, args.keep, started)
    print()
    reject_ok = case_reject(w, job_id, base, args.timeout_min, args.keep, started)

    print()
    if happy_ok and reject_ok:
        print(f"RESULT: PASS - the chain builds a deck on a good input and skips-not-fails on a "
              f"rejected one ({time.time()-started:.1f}s)")
        return 0
    print(f"RESULT: FAIL - happy={'PASS' if happy_ok else 'FAIL'}  "
          f"reject={'PASS' if reject_ok else 'FAIL'}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
