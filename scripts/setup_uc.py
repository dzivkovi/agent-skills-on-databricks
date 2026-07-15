"""
One-command Unity Catalog setup: create the schema and the input/output volumes
this project needs. Idempotent - safe to run repeatedly (existing objects are skipped).

Run this ONCE per workspace before the first `databricks bundle deploy`.

Usage:
    python scripts/setup_uc.py
    python scripts/setup_uc.py --profile coldstart --catalog workspace --schema genai
"""
import argparse
import os

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import DatabricksError
from databricks.sdk.service.catalog import VolumeType


def ensure_schema(w, catalog, schema):
    full = f"{catalog}.{schema}"
    try:
        w.schemas.create(name=schema, catalog_name=catalog, comment="GenAI skill inputs/outputs")
        print(f"created schema {full}")
    except DatabricksError as e:
        if "already exists" in str(e).lower():
            print(f"schema {full} already exists (ok)")
        else:
            raise


def ensure_volume(w, catalog, schema, name, comment):
    full = f"{catalog}.{schema}.{name}"
    try:
        w.volumes.create(catalog_name=catalog, schema_name=schema, name=name,
                         volume_type=VolumeType.MANAGED, comment=comment)
        print(f"created volume {full}  ->  /Volumes/{catalog}/{schema}/{name}")
    except DatabricksError as e:
        if "already exists" in str(e).lower():
            print(f"volume {full} already exists (ok)")
        else:
            raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE", "coldstart"))
    ap.add_argument("--catalog", default=os.environ.get("DATABRICKS_CATALOG", "workspace"))
    ap.add_argument("--schema", default=os.environ.get("DATABRICKS_SCHEMA", "genai"))
    args = ap.parse_args()

    w = WorkspaceClient(profile=args.profile)
    ensure_schema(w, args.catalog, args.schema)
    ensure_volume(w, args.catalog, args.schema, "input", "Input documents dropped by users")
    ensure_volume(w, args.catalog, args.schema, "output", "Generated files for users to download")
    print("\nUnity Catalog is ready. Next: databricks bundle deploy -p", args.profile)


if __name__ == "__main__":
    main()
