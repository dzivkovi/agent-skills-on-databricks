"""
End-to-end integration test for MULTI-SKILL reuse (#6) - the full ETL, not a unit test.

The counterpart to e2e_test.py (single happy path) and e2e_reject_test.py (dead-letter). This
one proves the headline claim of #6 against the DEPLOYED pipeline: two independent skills, each
published to the shared UC volume, are consumed INDEPENDENTLY by the same job via --skill-dir,
and each produces ITS OWN skill-specific output. Full round-trip through real volumes + the
inside-Databricks LLM, no UI:

    publish both skills to the volume
      -> for each skill: drop an input, run the deployed job with --skill-dir <that skill>
        -> wait for terminal state, get the output, ASSERT it carries that skill's OWN metric
          -> assert the two skills produced DISTINCT output files (independent consume)
            -> clean up

Each skill uses a DISTINCT input stem, so the assertion holds regardless of the deployed
runner's output-naming scheme (skill-namespaced or legacy). Exit 0 = PASS, 1 = FAIL.

    python scripts/e2e_multiskill_test.py --profile coldstart
    python scripts/e2e_multiskill_test.py --profile coldstart --keep   # leave files to inspect

The job id is resolved from THIS folder's deployed bundle (via `databricks bundle summary`),
or pass --job-id to skip resolution (useful from a worktree with no deployment state).
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

# (skill folder name, a signature metric only THAT skill emits) - the proof each skill ran.
SKILLS = [
    ("document-insights", "word count"),   # document-insights metrics table has "word count"
    ("readability", "flesch"),             # readability metrics table has flesch reading/kincaid
]

# A document with long, multi-syllable sentences so both skills produce meaningful metrics.
DOC = (b"# Weekly note\n\n"
       b"The quarterly infrastructure modernization initiative substantially accelerated "
       b"throughput while simultaneously diminishing operational expenditure across the "
       b"heterogeneous distributed environments our organization operates.\n")


def resolve_job_id(profile: str, resource_key: str) -> int:
    out = subprocess.run(
        ["databricks", "bundle", "summary", "-o", "json", "-p", profile],
        capture_output=True, text=True, check=True,
    ).stdout
    jobs = json.loads(out).get("resources", {}).get("jobs", {})
    if resource_key not in jobs:
        raise SystemExit(f"FAIL: job resource '{resource_key}' not found in bundle summary. "
                         f"Run `databricks bundle deploy -p {profile}` from this folder, or pass --job-id.")
    return int(jobs[resource_key]["id"])


def override(params, flag, value):
    p = list(params)
    if flag in p:
        p[p.index(flag) + 1] = value
    else:
        p += [flag, value]
    return p


def main():
    ap = argparse.ArgumentParser(description="Black-box e2e test of multi-skill reuse from the volume.")
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE", "coldstart"))
    ap.add_argument("--catalog", default=os.environ.get("DATABRICKS_CATALOG", "workspace"))
    ap.add_argument("--schema", default=os.environ.get("DATABRICKS_SCHEMA", "genai"))
    ap.add_argument("--resource-key", default="mvp0_weekly_report")
    ap.add_argument("--job-id", type=int, default=None, help="Skip bundle-summary resolution.")
    ap.add_argument("--timeout-min", type=int, default=10)
    ap.add_argument("--publish/--no-publish", dest="publish", default=True, action="store_true",
                    help="Publish both skills to the volume first (default on).")
    ap.add_argument("--keep", action="store_true", help="Do not delete the test files afterward.")
    args = ap.parse_args()

    w = WorkspaceClient(profile=args.profile)
    job_id = args.job_id or resolve_job_id(args.profile, args.resource_key)
    base = f"/Volumes/{args.catalog}/{args.schema}"
    out_dir = f"{base}/output"
    deployed = list(w.jobs.get(job_id=job_id).settings.tasks[0].spark_python_task.parameters or [])
    started = time.time()

    def step(msg):
        print(f"[multiskill +{time.time()-started:5.1f}s] {msg}")

    step(f"job_id={job_id}")

    # 0) Publish both skills to the shared volume (install-once), unless told not to.
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if args.publish:
        import importlib.util
        pub_path = os.path.join(repo_root, "scripts", "publish_skill.py")
        spec = importlib.util.spec_from_file_location("publish_skill", pub_path)
        publish_skill = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(publish_skill)
        publish_skill.ensure_skills_volume(w, args.catalog, args.schema)
        for name, _ in SKILLS:
            dest = f"{base}/skills/{name}"
            n = publish_skill.upload_skill_folder(w, os.path.join(repo_root, "skills", name), dest)
            step(f"published {name} ({n} files) -> {dest}")

    token = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    results = []   # (skill, out_path, ok)
    all_ok = True

    for name, signature in SKILLS:
        stem = f"multiskill-{name}-{token}"          # DISTINCT stem per skill
        in_path = f"{base}/input/{stem}.md"
        skill_dir = f"{base}/skills/{name}"
        print()
        step(f"SKILL '{name}': consume from {skill_dir}")
        w.files.upload(in_path, io.BytesIO(DOC), overwrite=True)

        params = override(deployed, "--in-path", in_path)
        params = override(params, "--skill-dir", skill_dir)   # <-- select this skill from the volume
        run = w.jobs.run_now(job_id=job_id, python_params=params).result(
            timeout=datetime.timedelta(minutes=args.timeout_min))
        state = run.state.result_state.value if run.state and run.state.result_state else "UNKNOWN"
        step(f"  run finished: result_state={state}  url={run.run_page_url}")

        matches = [e.path for e in w.files.list_directory_contents(out_dir)
                   if e.path.rsplit("/", 1)[-1].startswith(stem)]
        out_path = matches[0] if matches else None
        content = w.files.download(out_path).contents.read().decode("utf-8") if out_path else ""
        has_sig = signature.lower() in content.lower()
        refs_input = f"{stem}.md" in content
        ok = state == "SUCCESS" and out_path is not None and has_sig and refs_input
        step(f"  success={state=='SUCCESS'}  output={'yes' if out_path else 'MISSING'}  "
             f"has '{signature}'={has_sig}  refs_input={refs_input}  -> {'PASS' if ok else 'FAIL'}")
        results.append((name, out_path, ok))
        all_ok = all_ok and ok

    # Independent consume: the two skills produced DISTINCT output files.
    paths = [p for _, p, _ in results if p]
    distinct = len(set(paths)) == len(paths) and len(paths) == len(SKILLS)
    print()
    step(f"distinct outputs (independent consume): {distinct}  ({[os.path.basename(p) for p in paths]})")
    all_ok = all_ok and distinct

    if not args.keep:
        for name, out_path, _ in results:
            stem = f"multiskill-{name}-{token}"
            for p in (f"{base}/input/{stem}.md", out_path):
                if p:
                    try:
                        w.files.delete(p)
                    except Exception as e:  # noqa: BLE001 - cleanup is best-effort
                        step(f"  cleanup warning for {p}: {e}")
        step("cleaned up test files")

    print()
    if all_ok:
        print(f"RESULT: PASS - both skills consumed independently from the volume ({time.time()-started:.1f}s)")
        return 0
    print("RESULT: FAIL - a skill did not run independently from the volume")
    return 1


if __name__ == "__main__":
    sys.exit(main())
