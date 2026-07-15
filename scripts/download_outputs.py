"""
List and download files from the OUTBOX (deliverables) Unity Catalog volume.

Same reason as upload_input.py: the SDK avoids the Windows Git Bash path-munging
that breaks `databricks fs cp` for /Volumes paths.

Usage:
    python scripts/download_outputs.py                 # list + download all to ./_outbox/
    python scripts/download_outputs.py --list-only     # just list
    python scripts/download_outputs.py --profile coldstart --dest ./_outbox
"""
import argparse
import os

from databricks.sdk import WorkspaceClient

OUTBOX = "/Volumes/workspace/genai/deliverables"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="coldstart")
    ap.add_argument("--dest", default="./_outbox")
    ap.add_argument("--list-only", action="store_true")
    args = ap.parse_args()

    w = WorkspaceClient(profile=args.profile)
    entries = list(w.files.list_directory_contents(OUTBOX))
    if not entries:
        print(f"(outbox empty: {OUTBOX})")
        return

    print(f"Files in {OUTBOX}:")
    for e in entries:
        print(f"  {e.path}  ({e.file_size} bytes)")
    if args.list_only:
        return

    os.makedirs(args.dest, exist_ok=True)
    for e in entries:
        data = w.files.download(e.path).contents.read()
        local = os.path.join(args.dest, os.path.basename(e.path))
        with open(local, "wb") as f:
            f.write(data)
        print(f"DOWNLOADED {e.path} -> {local}")


if __name__ == "__main__":
    main()
