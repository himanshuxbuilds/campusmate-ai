"""Ingest (or re-ingest) the policy document.   python scripts/ingest_policy.py [path] [--force]
Uses POLICY_DOCUMENT_PATH when no path is given. Replace the handbook by pointing it at another file."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db
from app.services.ingest_service import ingest_policy

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    db.init_engine()
    with db.session_scope() as s:
        print(ingest_policy(s, args[0] if args else None, force="--force" in sys.argv))
