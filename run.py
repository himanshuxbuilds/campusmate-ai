"""Start the app (development): python run.py   -> http://localhost:8000
First run in development also creates the database, seeds fictional demo data and ingests the handbook.
Production: use a real ASGI server (see README) and create users with scripts/create_user.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import uvicorn
from sqlalchemy import select

from app import config, db
from app.models import PolicyDocument, User


def bootstrap() -> None:
    from scripts.init_db import upgrade
    upgrade()
    db.init_engine()
    with db.session_scope() as s:
        if config.IS_DEV and not s.execute(select(User.id).limit(1)).first():
            from app.services.seed_service import seed_demo_data
            print("seeded demo data:", seed_demo_data(s))
        if not s.execute(select(PolicyDocument.id).limit(1)).first():
            from app.services.ingest_service import ingest_policy
            print("ingested handbook:", ingest_policy(s))


if __name__ == "__main__":
    bootstrap()
    uvicorn.run("app.main:app", host=sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1", port=8000, reload=False)
