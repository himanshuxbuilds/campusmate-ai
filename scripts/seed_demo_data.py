"""Seed fictional demo data (development only).   python scripts/seed_demo_data.py [--force-production]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import db
from app.services.seed_service import SeedRefused, seed_demo_data

if __name__ == "__main__":
    db.init_engine()
    try:
        with db.session_scope() as s:
            print(seed_demo_data(s, force="--force-production" in sys.argv))
    except SeedRefused as e:
        sys.exit(f"refused: {e}")
