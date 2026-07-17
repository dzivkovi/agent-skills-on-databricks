"""
One-command smoke test of the WHOLE system - the gate every feature passes before "done".

Runs the creds-free pytest suite first (fast fail), then EVERY scripts/e2e_*.py live suite
against the deployed bundle. New e2e suites are auto-discovered: drop an e2e_<name>.py into
scripts/ and smoke covers it - nothing to register, nothing to forget. Exit codes are checked
directly (never through a pipe, which would mask a red exit).

    python scripts/smoke.py --profile coldstart
    python scripts/smoke.py --only reject          # substring filter on suite names
    python scripts/smoke.py --skip-pytest          # live suites only

Exit code 0 = every suite passed. Anything else = the system is NOT working end to end,
whatever the unit tests say.
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser(description="Full-system smoke: pytest + every scripts/e2e_*.py.")
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE", "coldstart"))
    ap.add_argument("--only", default="", help="Run only e2e suites whose name contains this substring.")
    ap.add_argument("--skip-pytest", action="store_true", help="Skip the local pytest stage.")
    args = ap.parse_args()

    suites = []
    if not args.skip_pytest:
        suites.append(("pytest", [sys.executable, "-m", "pytest", str(ROOT / "tests"), "-q"]))
    for p in sorted((ROOT / "scripts").glob("e2e_*.py")):
        if args.only and args.only not in p.stem:
            continue
        suites.append((p.stem, [sys.executable, str(p), "--profile", args.profile]))

    results, started = [], time.time()
    for name, cmd in suites:
        print(f"\n[smoke] === {name} ===")
        t0 = time.time()
        code = subprocess.run(cmd, cwd=ROOT).returncode  # direct exit code, no pipes
        results.append((name, code, time.time() - t0))

    print("\n" + "=" * 56)
    for name, code, dt in results:
        print(f"  {'PASS' if code == 0 else 'FAIL':4}  {name:32} {dt:6.0f}s")
    failed = [name for name, code, _ in results if code != 0]
    verdict = "PASS" if not failed else f"FAIL ({', '.join(failed)})"
    print(f"SMOKE: {verdict} - {len(results) - len(failed)}/{len(results)} suites in {time.time() - started:.0f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
