"""
Upload a local file into the INPUT Unity Catalog volume.

Why this exists: on Windows Git Bash, `databricks fs cp /Volumes/...` mangles the
path (into C:\\Program Files\\Git\\Volumes\\...). The Python SDK takes the volume
path as a plain string, so it is the reliable cross-platform way to move files.

Profile/catalog/schema default from environment variables (see .env.example) and
can be overridden with flags, so this works in any workspace.

Usage:
    python scripts/upload_input.py samples/weekly-update.md
    python scripts/upload_input.py <local-file> [--profile P] [--catalog C] [--schema S] [--dest-name NAME]
"""
import argparse
import os

from databricks.sdk import WorkspaceClient


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("local_file")
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE", "coldstart"))
    ap.add_argument("--catalog", default=os.environ.get("DATABRICKS_CATALOG", "workspace"))
    ap.add_argument("--schema", default=os.environ.get("DATABRICKS_SCHEMA", "genai"))
    ap.add_argument("--dest-name", default=None, help="Name in the volume (defaults to the local filename)")
    args = ap.parse_args()

    input_vol = f"/Volumes/{args.catalog}/{args.schema}/input"
    w = WorkspaceClient(profile=args.profile)
    dest = f"{input_vol}/{args.dest_name or os.path.basename(args.local_file)}"
    with open(args.local_file, "rb") as f:
        w.files.upload(dest, f, overwrite=True)
    print(f"UPLOADED {args.local_file} -> {dest}")


if __name__ == "__main__":
    main()
