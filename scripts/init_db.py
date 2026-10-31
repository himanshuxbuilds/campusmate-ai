"""Create/upgrade the database schema with Alembic migrations.   python scripts/init_db.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from alembic import command
from alembic.config import Config

from app import config


def upgrade(url: str | None = None) -> None:
    cfg = Config(str(config.ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(config.ROOT / "migrations"))
    cfg.attributes["url"] = url or config.DATABASE_URL
    command.upgrade(cfg, "head")


if __name__ == "__main__":
    upgrade()
    print(f"database ready: {config.DATABASE_URL}")
