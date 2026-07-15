"""
Publish (register) a skill folder ONCE to a shared Unity Catalog volume, so any job can
consume it WITHOUT bundling a copy. This is the "install once" half of skill reuse - the
Databricks equivalent of dropping a skill into ~/.claude/skills (see
docs/skill-reuse-on-databricks.md).

    python scripts/publish_skill.py skills/document-insights --profile coldstart
    -> /Volumes/<catalog>/<schema>/skills/document-insights/  (SKILL.md + scripts/...)

Update flow: re-run this after editing the skill; consumers pick up the new version on their
next run. No per-consumer redeploy.
"""
import argparse
import os

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import DatabricksError
from databricks.sdk.service.catalog import VolumeType

SKIP_DIRS = {"__pycache__", ".git"}


def ensure_skills_volume(w, catalog, schema):
    try:
        w.volumes.create(catalog_name=catalog, schema_name=schema, name="skills",
                         volume_type=VolumeType.MANAGED,
                         comment="Shared skills - published once, reused by any job")
    except DatabricksError as e:
        if "already exists" not in str(e).lower():
            raise


def main():
    ap = argparse.ArgumentParser(description="Publish a skill folder to a shared UC volume.")
    ap.add_argument("skill_dir", help="Local skill folder, e.g. skills/document-insights")
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE", "coldstart"))
    ap.add_argument("--catalog", default=os.environ.get("DATABRICKS_CATALOG", "workspace"))
    ap.add_argument("--schema", default=os.environ.get("DATABRICKS_SCHEMA", "genai"))
    args = ap.parse_args()

    w = WorkspaceClient(profile=args.profile)
    ensure_skills_volume(w, args.catalog, args.schema)

    name = os.path.basename(os.path.normpath(args.skill_dir))
    dest_root = f"/Volumes/{args.catalog}/{args.schema}/skills/{name}"

    count = 0
    for root, dirs, files in os.walk(args.skill_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if fname.endswith((".pyc",)):
                continue
            local = os.path.join(root, fname)
            rel = os.path.relpath(local, args.skill_dir).replace(os.sep, "/")
            dest = f"{dest_root}/{rel}"
            with open(local, "rb") as fh:
                w.files.upload(dest, fh, overwrite=True)
            print(f"  {rel}")
            count += 1

    print(f"\nPUBLISHED {count} files -> {dest_root}")
    print(f"Consume it from any job with:  --skill-dir {dest_root}")


if __name__ == "__main__":
    main()
