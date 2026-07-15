"""
Negative integration test: bad / unsafe inputs must be QUARANTINED to the dead-letter queue
while the batch job still SUCCEEDS. Complements the happy-path scripts/e2e_test.py.

Two cases:
  1. structural - an empty / whitespace-only input (rejected before any model call).
  2. content   - a document containing PII, which the LLM content guardrail flags.

No offensive content is ever used. PII detection is THE enterprise guardrail, and it is tested
with obviously-fake, reserved-for-testing values (email @example.com, SSN 123-45-6789, the Visa
test card 4111 1111 1111 1111, the 555-01xx fictional phone range) - safe to commit to a public repo.

Exit code 0 = every bad input was quarantined correctly, 1 = a failure.
    python scripts/reject_test.py --profile coldstart
"""
import argparse
import datetime
import io
import json
import os
import subprocess
import sys

from databricks.sdk import WorkspaceClient

CASES = [
    ("empty", b"   \n\t  \n"),
    ("pii", b"Please contact Jane Doe at jane.doe@example.com or 555-0123. "
            b"Her SSN is 123-45-6789 and test card 4111 1111 1111 1111."),
]


def resolve_job_id(profile: str, key: str) -> int:
    out = subprocess.run(["databricks", "bundle", "summary", "-o", "json", "-p", profile],
                         capture_output=True, text=True, check=True).stdout
    return int(json.loads(out)["resources"]["jobs"][key]["id"])


def override(params, flag, value):
    p = list(params)
    if flag in p:
        p[p.index(flag) + 1] = value
    else:
        p += [flag, value]
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE", "coldstart"))
    ap.add_argument("--catalog", default=os.environ.get("DATABRICKS_CATALOG", "workspace"))
    ap.add_argument("--schema", default=os.environ.get("DATABRICKS_SCHEMA", "genai"))
    ap.add_argument("--resource-key", default="mvp0_weekly_report")
    ap.add_argument("--timeout-min", type=int, default=10)
    args = ap.parse_args()

    w = WorkspaceClient(profile=args.profile)
    job_id = resolve_job_id(args.profile, args.resource_key)
    base = f"/Volumes/{args.catalog}/{args.schema}"
    deployed = list(w.jobs.get(job_id=job_id).settings.tasks[0].spark_python_task.parameters or [])

    all_ok = True
    for label, payload in CASES:
        token = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"reject-{label}-{token}.md"
        in_path = f"{base}/input/{name}"
        w.files.upload(in_path, io.BytesIO(payload), overwrite=True)

        params = override(deployed, "--in-path", in_path)
        run = w.jobs.run_now(job_id=job_id, python_params=params).result(
            timeout=datetime.timedelta(minutes=args.timeout_min))
        state = run.state.result_state.value if run.state and run.state.result_state else "UNKNOWN"

        rejected = [e.path for e in w.files.list_directory_contents(f"{base}/rejected")]
        quarantined = any(p.endswith(name) for p in rejected)
        reason_ok = any(p.endswith(name + ".reason.txt") for p in rejected)
        reason = ""
        if reason_ok:
            reason = w.files.download(f"{base}/rejected/{name}.reason.txt").contents.read().decode().strip()
        outs = [e.path for e in w.files.list_directory_contents(f"{base}/output")]
        no_output = not any(name.rsplit(".", 1)[0] in p for p in outs)

        ok = state == "SUCCESS" and quarantined and reason_ok and no_output
        all_ok = all_ok and ok
        print(f"[{label:9s}] job={state} quarantined={quarantined} no_output={no_output}")
        print(f"            reason: {reason}")
        print(f"            -> {'PASS' if ok else 'FAIL'}")

        for p in (in_path, f"{base}/rejected/{name}", f"{base}/rejected/{name}.reason.txt"):
            try:
                w.files.delete(p)
            except Exception:  # noqa: BLE001 - cleanup is best-effort
                pass

    print("\nRESULT:", "PASS - all bad inputs quarantined, batch succeeded" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
