import sqlite3
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app import config, db, models
from app.services import seed_service
from scripts.init_db import upgrade


def tables(url):
    c = sqlite3.connect(url.replace("sqlite:///", ""))
    return {r[0] for r in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")}


def test_migration_creates_every_model_table_and_downgrades_cleanly(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    upgrade(url)
    assert tables(url) == set(db.Base.metadata.tables) | {"alembic_version"} and len(db.Base.metadata.tables) == 18
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(config.ROOT / "alembic.ini")); cfg.set_main_option("script_location", str(config.ROOT / "migrations")); cfg.attributes["url"] = url
    command.downgrade(cfg, "base")
    assert tables(url) == {"alembic_version"}


def test_models_and_migration_have_not_drifted(tmp_path):
    url = f"sqlite:///{tmp_path / 'd.db'}"
    upgrade(url)
    eng = create_engine(url)
    with eng.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), db.Base.metadata)
    assert diff == [], diff                                                              # run `alembic revision --autogenerate` if this fails


def test_spec_tables_exist(database):
    spec = {"users", "student_records", "fee_records", "scholarship_records", "support_tickets", "conversations", "conversation_messages", "memories", "policy_documents", "policy_chunks",
            "approval_requests", "agent_traces", "audit_events"}
    assert spec <= tables(database)


def test_foreign_keys_are_enforced(session):
    session.add(models.StudentRecord(student_id="NOBODY", academic_year="2026-27", programme="p", programme_name="p", batch="b", current_trimester="IV"))
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize("make", [
    lambda: models.User(user_code="x1", name="X", password_hash="h", role="superuser"),
    lambda: models.ApprovalRequest(request_number="APR-X", requester_id="S2025AI001", action_type="A", target_resource_type="t", target_resource_id="i", proposed_change={}, status="DONE", proposal_hash="h", expires_at=db.utcnow()),
    lambda: models.FeeRecord(student_id="S2025AI003", academic_year="2030-31", total_tuition=1, total_fee=-5, amount_paid=0, amount_due=0, status="PAID"),
    lambda: models.ConversationMessage(conversation_id="x", role="system", message_content="m")])
def test_check_constraints_reject_invalid_rows(session, make):
    session.add(make())
    with pytest.raises(IntegrityError):
        session.flush()


def test_unique_constraints(session):
    session.add(models.User(user_code="S2025AI001", name="Dup", password_hash="h", role="student"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    t = session.execute(text("select ticket_number from support_tickets limit 1")).scalar_one()
    session.add(models.SupportTicket(ticket_number=t, student_id="S2025AI001", category="FEES", subject="dup number"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_expected_indexes_exist(database):
    c = sqlite3.connect(database.replace("sqlite:///", ""))
    idx = {r[0] for r in c.execute("select name from sqlite_master where type='index'")}
    assert {"ix_audit_events_created_at", "ix_audit_events_event_type", "ix_support_tickets_ticket_number", "ix_agent_traces_trace_id", "ix_approval_requests_status"} <= idx
    assert c.execute("PRAGMA foreign_key_list(support_tickets)").fetchall()


def test_seeding_is_dev_only_and_idempotent(session, monkeypatch):
    assert seed_service.seed_demo_data(session) == {"status": "already_seeded"}
    monkeypatch.setattr(config, "IS_DEV", False)
    with pytest.raises(seed_service.SeedRefused):
        seed_service.seed_demo_data(session)
    assert seed_service.seed_demo_data(session, force=True)


def test_sqlite_uses_immediate_transactions_and_wal(database):
    with db.engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1 and c.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"
