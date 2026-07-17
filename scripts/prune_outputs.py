"""
Volume janitor: delete run artifacts older than N days from the output volume.

Runs accumulate files - reports, decks, chain manifests under output/_runs/, and leftover
--keep test debris. This is the standalone cleanup for that, in the spirit of
`docker image prune --filter until=`: age-based, and DRY-RUN by default so you always see what
would go before anything is deleted.

    python scripts/prune_outputs.py --profile coldstart                # show what is >7 days old
    python scripts/prune_outputs.py --profile coldstart --older-than 3 --yes   # actually delete
    python scripts/prune_outputs.py --profile coldstart --include-rejected --yes

It is NOT part of the test gate: scripts/smoke.py auto-discovers e2e_*.py, not prune_*.py, so
this never runs as a side effect of testing. It leaves the input volume alone, and by default
leaves the rejected queue alone too - that queue is an audit trail an operator inspects, so you
opt into clearing it with --include-rejected. The newest files always survive: they are not old.
"""
import argparse
import datetime
import os

from databricks.sdk import WorkspaceClient

DAY_MS = 86_400_000


def older_than(last_modified_ms, now_ms, days) -> bool:
    """Is this file older than `days`? Unknown mtime (None) is treated as NOT old, so a file we
    cannot date is never deleted - the janitor only ever removes what it can prove is stale."""
    if last_modified_ms is None:
        return False
    return (now_ms - last_modified_ms) > days * DAY_MS


def iter_files(w, root):
    """Yield (path, last_modified_ms, size) for every file under a volume dir, recursively."""
    stack = [root]
    while stack:
        try:
            entries = list(w.files.list_directory_contents(stack.pop()))
        except Exception:  # noqa: BLE001 - a missing volume just means nothing to prune
            continue
        for e in entries:
            if getattr(e, "is_directory", False):
                stack.append(e.path)
            else:
                yield e.path, e.last_modified, (e.file_size or 0)


def main():
    ap = argparse.ArgumentParser(description="Prune output-volume artifacts older than N days.")
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE", "coldstart"))
    ap.add_argument("--catalog", default=os.environ.get("DATABRICKS_CATALOG", "workspace"))
    ap.add_argument("--schema", default=os.environ.get("DATABRICKS_SCHEMA", "genai"))
    ap.add_argument("--older-than", type=int, default=7, metavar="DAYS",
                    help="Delete files older than this many days (default 7).")
    ap.add_argument("--include-rejected", action="store_true",
                    help="Also prune the rejected queue (off by default - it is an audit trail).")
    ap.add_argument("--yes", action="store_true",
                    help="Actually delete. Without it, this only reports what WOULD be deleted.")
    args = ap.parse_args()

    w = WorkspaceClient(profile=args.profile)
    base = f"/Volumes/{args.catalog}/{args.schema}"
    targets = [f"{base}/output"] + ([f"{base}/rejected"] if args.include_rejected else [])
    now_ms = int(datetime.datetime.now().timestamp() * 1000)

    stale, freed = [], 0
    for root in targets:
        for path, mtime, size in iter_files(w, root):
            if older_than(mtime, now_ms, args.older_than):
                age = (now_ms - mtime) / DAY_MS
                stale.append(path)
                freed += size
                verb = "delete" if args.yes else "would delete"
                print(f"  {verb}  {age:5.1f}d  {path[len(base) + 1:]}")

    if not stale:
        print(f"Nothing older than {args.older_than} days. Clean.")
        return 0

    if args.yes:
        for path in stale:
            try:
                w.files.delete(path)
            except Exception as e:  # noqa: BLE001 - best effort; report and continue
                print(f"  warning: could not delete {path}: {e}")
        # Remove now-empty _runs/<id>/ directories so the manifest scaffolding does not pile up.
        for d in _empty_run_dirs(w, f"{base}/output/_runs"):
            try:
                w.files.delete_directory(d)
            except Exception:  # noqa: BLE001
                pass
        print(f"\nPRUNED {len(stale)} file(s), ~{freed / 1024:.0f} KB freed.")
    else:
        print(f"\n{len(stale)} file(s), ~{freed / 1024:.0f} KB would be freed. "
              f"Re-run with --yes to delete.")
    return 0


def _empty_run_dirs(w, runs_root):
    """Run directories under _runs/ that have no files left (their manifest was just pruned)."""
    empties = []
    try:
        for e in w.files.list_directory_contents(runs_root):
            if getattr(e, "is_directory", False) and not any(True for _ in iter_files(w, e.path)):
                empties.append(e.path)
    except Exception:  # noqa: BLE001 - no _runs dir yet
        pass
    return empties


if __name__ == "__main__":
    raise SystemExit(main())
