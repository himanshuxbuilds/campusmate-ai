"""Delete data older than the retention policy (docs/retention_policy.md).   python scripts/purge_retention.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db
from app.services import audit_service, retention_service

if __name__ == "__main__":
    db.init_engine()
    with db.session_scope() as s:
        out = retention_service.purge(s)
        audit_service.record(s, "ADMIN_ACTION", "retention.purge", "SUCCESS", actor_id="scripts/purge_retention.py", actor_role="system", details=out)
    print("purged:", out)
