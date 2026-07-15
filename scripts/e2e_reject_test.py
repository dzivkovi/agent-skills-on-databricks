"""
Negative end-to-end test - the counterpart to e2e_test.py (the positive / happy path).

Bad or unsafe inputs must be QUARANTINED to the dead-letter queue while the batch job still
SUCCEEDS (one bad record never fails the run). Two cases:

    empty  -> a whitespace-only file            (structural reject, before any model call)
    pii    -> a document containing PII          (LLM content-guardrail reject)

No offensive content is ever used. PII detection is THE enterprise guardrail, and it is tested
with obviously-fake, reserved-for-testing values (email @example.com, SSN 123-45-6789, the Visa
test card 4111 1111 1111 1111, the 555-01xx fictional phone range) - safe to commit to a public repo.

Exit code 0 = every bad input was quarantined correctly, 1 = a failure. Run after `bundle deploy`:

    python scripts/e2e_reject_test.py --profile coldstart          # runs and cleans up
    python scripts/e2e_reject_test.py --profile coldstart --keep   # leaves the quarantined files to inspect
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

# (label, why, payload) - payload bytes are the file contents dropped into the input volume.
CASES = [
    ("empty", "whitespace-only - structural reject", b"   \n\t  \n"),
    ("pii", "fake PII - content-guardrail reject",
     b"Please contact Jane Doe at jane.doe@example.com or 555-0123. "
     b"Her SSN is 123-45-6789 and test card 4111 1111 1111 1111."),
]


def resolve_job_id(profile: str, resource_key: str) -> int:
    out = subprocess.run(
        ["databricks", "bundle", "summary", "-o", "json", "-p", profile],
        capture_output=True, text=True, check=True,
    ).stdout
    jobs = json.loads(out).get("resources", {}).get("jobs", {})
    if resource_key not in jobs:
        raise SystemExit(f"FAIL: job resource '{resource_key}' not found. "
                         f"Did you run `databricks bundle deploy -p {profile}` from this folder?")
    return int(jobs[resource_key]["id"])


def override(params, flag, value):
    p = list(params)
    if flag in p:
        p[p.index(flag) + 1] = value
    else:
        p += [flag, value]
    return p


def main():
    ap = argparse.ArgumentParser(description="Negative black-box e2e test (dead-letter queue).")
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE", "coldstart"))
    ap.add_argument("--catalog", default=os.environ.get("DATABRICKS_CATALOG", "workspace"))
    ap.add_argument("--schema", default=os.environ.get("DATABRICKS_SCHEMA", "genai"))
    ap.add_argument("--resource-key", default="mvp0_weekly_report")
    ap.add_argument("--timeout-min", type=int, default=10)
    ap.add_argument("--keep", action="store_true",
                    help="Leave the quarantined files in the rejected volume so you can inspect them.")
    args = ap.parse_args()

    w = WorkspaceClient(profile=args.profile)
    job_id = resolve_job_id(args.profile, args.resource_key)
    base = f"/Volumes/{args.catalog}/{args.schema}"
    deployed = list(w.jobs.get(job_id=job_id).settings.tasks[0].spark_python_task.parameters or [])
    started = time.time()

    def step(msg):
        print(f"[reject +{time.time()-started:5.1f}s] {msg}")

    step(f"job_id={job_id}  rejected volume={base}/rejected")

    all_ok = True
    for label, why, payload in CASES:
        token = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"reject-{label}-{token}.md"
        in_path = f"{base}/input/{name}"

        print()
        step(f"CASE '{label}' ({why})")
        w.files.upload(in_path, io.BytesIO(payload), overwrite=True)
        step(f"  uploaded bad input -> {in_path}")

        # Preserve the job's deployed params (model, --skill-dir, --rejected-dir); redirect only input.
        params = override(deployed, "--in-path", in_path)
        step("  triggering job run and waiting for terminal state...")
        run = w.jobs.run_now(job_id=job_id, python_params=params).result(
            timeout=datetime.timedelta(minutes=args.timeout_min))
        state = run.state.result_state.value if run.state and run.state.result_state else "UNKNOWN"
        step(f"  run finished: result_state={state}  url={run.run_page_url}")

        rejected = [e.path for e in w.files.list_directory_contents(f"{base}/rejected")]
        quarantined = any(p.endswith(name) for p in rejected)
        reason_ok = any(p.endswith(name + ".reason.txt") for p in rejected)
        reason = ""
        if reason_ok:
            reason = w.files.download(f"{base}/rejected/{name}.reason.txt").contents.read().decode().strip()
        outs = [e.path for e in w.files.list_directory_contents(f"{base}/output")]
        no_output = not any(name.rsplit(".", 1)[0] in p for p in outs)

        step(f"  batch_succeeded={state == 'SUCCESS'}  quarantined={quarantined}  "
             f"reason_sidecar={reason_ok}  no_output={no_output}")
        step(f"  reason: {reason}")
        ok = state == "SUCCESS" and quarantined and reason_ok and no_output
        step(f"  -> {'PASS' if ok else 'FAIL'}")
        all_ok = all_ok and ok

        if args.keep:
            step(f"  --keep: left {base}/rejected/{name} (+ .reason.txt) for inspection")
        else:
            for p in (in_path, f"{base}/rejected/{name}", f"{base}/rejected/{name}.reason.txt"):
                try:
                    w.files.delete(p)
                except Exception:  # noqa: BLE001 - cleanup is best-effort
                    pass

    print()
    if all_ok:
        print(f"RESULT: PASS - all bad inputs quarantined, batch succeeded ({time.time()-started:.1f}s)")
        if args.keep:
            host = w.config.host.rstrip("/")
            print(f"Inspect them in Catalog Explorer: "
                  f"{host}/explore/data/volumes/{args.catalog}/{args.schema}/rejected")
        return 0
    print("RESULT: FAIL - a bad input was not quarantined correctly")
    return 1


if __name__ == "__main__":
    sys.exit(main())
