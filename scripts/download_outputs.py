"""
List and download files from the OUTPUT Unity Catalog volume.

Same reason as upload_input.py: the SDK avoids the Windows Git Bash path-munging
that breaks `databricks fs cp` for /Volumes paths.

Profile/catalog/schema default from environment variables (see .env.example) and
can be overridden with flags, so this works in any workspace.

Usage:
    python scripts/download_outputs.py                 # list + download all to ./_output/
    python scripts/download_outputs.py --list-only     # just list
    python scripts/download_outputs.py [--profile P] [--catalog C] [--schema S] [--dest ./_output]
"""
import argparse
import os

from databricks.sdk import WorkspaceClient


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE", "coldstart"))
    ap.add_argument("--catalog", default=os.environ.get("DATABRICKS_CATALOG", "workspace"))
    ap.add_argument("--schema", default=os.environ.get("DATABRICKS_SCHEMA", "genai"))
    ap.add_argument("--dest", default="./_output")
    ap.add_argument("--list-only", action="store_true")
    args = ap.parse_args()

    output_vol = f"/Volumes/{args.catalog}/{args.schema}/output"
    w = WorkspaceClient(profile=args.profile)
    entries = list(w.files.list_directory_contents(output_vol))
    if not entries:
        print(f"(output empty: {output_vol})")
        return

    print(f"Files in {output_vol}:")
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
