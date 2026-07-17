"""
Publish (register) a skill folder ONCE to a shared Unity Catalog volume, so any job can
consume it WITHOUT bundling a copy. This is the "install once" half of skill reuse - the
Databricks equivalent of dropping a skill into ~/.claude/skills (see
docs/skill-reuse-on-databricks.md).

    python scripts/publish_skill.py skills/document-insights --profile coldstart
    -> /Volumes/<catalog>/<schema>/skills/document-insights/  (SKILL.md + scripts/...)

Update flow: re-run this after editing the skill; consumers pick up the new version on their
next run. No per-consumer redeploy. Publish is a MIRROR: a file you renamed or deleted locally
is removed from the volume too, so a stale file can never linger and get consumed (issue #10 -
a leftover run.py did exactly that during the #21 refactor).
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


def upload_skill_folder(w, skill_dir, dest_root) -> set:
    """Upload one skill folder to dest_root on the volume; return the set of relative paths
    published (the source of truth prune_stale mirrors against).

    Skips __pycache__/.git and compiled .pyc so caches never reach the shared volume. Each
    skill goes to its OWN dest_root, so publishing one skill never touches another's files -
    the isolation the multi-skill pattern (#6) depends on. Testable without a live workspace:
    pass any object whose files.upload(dest, fh, overwrite) does the write.
    """
    published = set()
    for root, dirs, files in os.walk(skill_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if fname.endswith((".pyc",)):
                continue
            local = os.path.join(root, fname)
            rel = os.path.relpath(local, skill_dir).replace(os.sep, "/")
            with open(local, "rb") as fh:
                w.files.upload(f"{dest_root}/{rel}", fh, overwrite=True)
            print(f"  {rel}")
            published.add(rel)
    return published


def _volume_files(w, root):
    """Every file path under a volume directory, recursively. Empty when root does not exist."""
    found, stack = [], [root]
    while stack:
        try:
            entries = list(w.files.list_directory_contents(stack.pop()))
        except Exception:  # noqa: BLE001 - a missing dir (first publish) just means nothing to prune
            continue
        for e in entries:
            (stack if getattr(e, "is_directory", False) else found).append(e.path)
    return found


def prune_stale(w, dest_root, published) -> list:
    """Delete volume files under dest_root that the latest publish did NOT write, so the
    published skill is a true mirror of the source. Scoped to this skill's own dest_root, so it
    can never touch another skill. Returns the paths removed."""
    keep = {f"{dest_root}/{rel}" for rel in published}
    removed = []
    for path in _volume_files(w, dest_root):
        if path not in keep:
            w.files.delete(path)
            print(f"  pruned {path[len(dest_root) + 1:]}")
            removed.append(path)
    return removed


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

    published = upload_skill_folder(w, args.skill_dir, dest_root)
    removed = prune_stale(w, dest_root, published)

    print(f"\nPUBLISHED {len(published)} files -> {dest_root}")
    if removed:
        print(f"PRUNED {len(removed)} stale file(s) no longer in the source")
    print(f"Consume it from any job with:  --skill-dir {dest_root}")


if __name__ == "__main__":
    main()
