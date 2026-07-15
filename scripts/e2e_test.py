"""
End-to-end integration test for the deployed pipeline (NOT a unit test).

Treats the Unity Catalog volumes as an S3-like black-box boundary and exercises the
whole platform headlessly (no UI login):

    put a unique file into the INPUT volume
      -> trigger the deployed Lakeflow job (which calls the inside-Databricks LLM)
        -> wait until the run reaches a terminal state
          -> get the result from the OUTPUT volume and ASSERT it is correct
            -> clean up the test files

Exit code 0 = PASS, 1 = FAIL. Run it after `databricks bundle deploy`:

    python scripts/e2e_test.py --profile coldstart

The job id is resolved from THIS folder's deployed bundle (via `databricks bundle
summary`), so it targets the right job even if another bundle deployed a same-named job.
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


def main():
    ap = argparse.ArgumentParser(description="Black-box e2e test of the deployed pipeline.")
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE", "coldstart"))
    ap.add_argument("--catalog", default=os.environ.get("DATABRICKS_CATALOG", "workspace"))
    ap.add_argument("--schema", default=os.environ.get("DATABRICKS_SCHEMA", "genai"))
    ap.add_argument("--resource-key", default="mvp0_weekly_report")
    ap.add_argument("--model", default="databricks-gpt-oss-120b")
    ap.add_argument("--job-id", type=int, default=None, help="Override auto-resolution.")
    ap.add_argument("--timeout-min", type=int, default=10)
    ap.add_argument("--keep", action="store_true", help="Do not delete the test files afterward.")
    args = ap.parse_args()

    w = WorkspaceClient(profile=args.profile)
    job_id = args.job_id or resolve_job_id(args.profile, args.resource_key)

    # Unique marker so this run cannot be confused with any other file in the bucket.
    token = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    in_path = f"/Volumes/{args.catalog}/{args.schema}/input/e2e-{token}.md"
    out_dir = f"/Volumes/{args.catalog}/{args.schema}/output"
    out_prefix = f"e2e-{token}-summary"
    started = time.time()

    def step(msg):
        print(f"[e2e +{time.time()-started:5.1f}s] {msg}")

    step(f"job_id={job_id}  input={in_path}")

    # 1) PUT: drop a unique document into the input (the 'S3 upload').
    payload = (f"# E2E test document {token}\n\n"
               f"- unique marker: E2E-{token}\n"
               f"- shipped a thing, cut latency by 30 percent, blocked on nothing\n").encode("utf-8")
    w.files.upload(in_path, io.BytesIO(payload), overwrite=True)
    step("uploaded test file to the input volume")

    # 2) TRIGGER: run the deployed job, overriding its params to point at OUR file.
    step("triggering job run and waiting for terminal state...")
    run = w.jobs.run_now(
        job_id=job_id,
        python_params=["--model", args.model, "--in-path", in_path, "--out-dir", out_dir],
    ).result(timeout=datetime.timedelta(minutes=args.timeout_min))

    state = run.state.result_state.value if run.state and run.state.result_state else "UNKNOWN"
    step(f"run finished: result_state={state}  url={run.run_page_url}")
    if state != "SUCCESS":
        print(f"\nRESULT: FAIL - job run did not succeed (state={state})")
        return 1

    # 3) GET + ASSERT: the expected output must exist in the output and reference our input.
    matches = [e for e in w.files.list_directory_contents(out_dir)
               if e.path.rsplit("/", 1)[-1].startswith(out_prefix)]
    if not matches:
        print(f"\nRESULT: FAIL - no output file '{out_prefix}*' found in output")
        return 1
    out_path = matches[0].path
    content = w.files.download(out_path).contents.read().decode("utf-8")
    step(f"retrieved output: {out_path} ({len(content)} chars)")

    checks = {
        "output non-empty": len(content.strip()) > 0,
        "output references our input file": f"e2e-{token}.md" in content,
    }
    for name, ok in checks.items():
        print(f"    assert {name}: {'ok' if ok else 'FAILED'}")

    # 4) CLEAN UP the test files (best effort) unless --keep.
    if not args.keep:
        for p in (in_path, out_path):
            try:
                w.files.delete(p)
            except Exception as e:  # noqa: BLE001 - cleanup is best-effort
                step(f"cleanup warning for {p}: {e}")
        step("cleaned up test files")

    if all(checks.values()):
        print(f"\nRESULT: PASS - full round-trip in {time.time()-started:.1f}s")
        return 0
    print("\nRESULT: FAIL - one or more assertions failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
