"""Make the repo's src/ and scripts/ importable from tests without packaging."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "src", ROOT / "scripts"):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)
