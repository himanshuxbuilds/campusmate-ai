"""All persistent entities. Fourteen spec tables plus supporting tables (see docs/data_model.md).

Foreign keys reference users.user_code (the student ID / staff username) so every record is
traceable to a person without exposing internal integer IDs in the API.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (JSON, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, String,
                        Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, utcnow

ROLES = ("student", "staff", "admin")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)          # student ID or staff username
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(160), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(16), index=True)
    programme: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    staff_scope: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)       # {"approval_actions": [...], "ticket_categories": [...]}
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (CheckConstraint("role in ('student','staff','admin')", name="ck_users_role"),)


class StudentRecord(Base):
    __tablename__ = "student_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.user_code"), index=True)
    academic_year: Mapped[str] = mapped_column(String(16))
    programme: Mapped[str] = mapped_column(String(32))
    programme_name: Mapped[str] = mapped_column(String(120))
    batch: Mapped[str] = mapped_column(String(16))
    current_trimester: Mapped[str] = mapped_column(String(8))
    year1_cgpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    attendance_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hosteller: Mapped[bool] = mapped_column(Boolean, default=False)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    mobile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    refund_bank_masked: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("student_id", "academic_year", name="uq_student_year"),)


class CourseRegistration(Base):
    __tablename__ = "course_registrations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.user_code"), index=True)
    course_name: Mapped[str] = mapped_column(String(160))
    trimester: Mapped[str] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(16))                                         # core | elective
    credits: Mapped[int] = mapped_column(Integer, default=3)
    status: Mapped[str] = mapped_column(String(16), default="REGISTERED")                 # REGISTERED | DROPPED
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class FeeRecord(Base):
    __tablename__ = "fee_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.user_code"), index=True)
    academic_year: Mapped[str] = mapped_column(String(16))
    total_tuition: Mapped[int] = mapped_column(Integer)
    caution_deposit: Mapped[int] = mapped_column(Integer, default=0)
    scholarship_adjustment_total: Mapped[int] = mapped_column(Integer, default=0)
    total_fee: Mapped[int] = mapped_column(Integer)
    amount_paid: Mapped[int] = mapped_column(Integer, default=0)
    amount_due: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20))                                       # PAID | PARTIALLY_PAID | UNPAID | OVERDUE
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (CheckConstraint("total_fee >= 0 AND amount_paid >= 0 AND amount_due >= 0", name="ck_fee_nonneg"),
                      UniqueConstraint("student_id", "academic_year", name="uq_fee_student_year"))


class FeeInstallment(Base):
    __tablename__ = "fee_installments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.user_code"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    due_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12))                                       # PAID | PENDING | OVERDUE
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    late_fee: Mapped[int | None] = mapped_column(Integer, nullable=True)
    late_fee_paid: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    __table_args__ = (CheckConstraint("amount >= 0", name="ck_inst_amount"), Index("ix_inst_student_due", "student_id", "due_date"))


class ScholarshipRecord(Base):
    __tablename__ = "scholarship_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.user_code"), index=True)
    scholarship_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    application_status: Mapped[str] = mapped_column(String(40))                           # NOT_APPLIED | SUBMITTED | UNDER_REVIEW | APPROVED | REJECTED | ADDITIONAL_DOCUMENTS_REQUIRED | WITHDRAWN
    academic_year: Mapped[str] = mapped_column(String(16))
    amount_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_adjusted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    decided_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_number: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.user_code"), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    subject: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(10), default="NORMAL")
    status: Mapped[str] = mapped_column(String(20), default="OPEN", index=True)
    programme: Mapped[str | None] = mapped_column(String(32), nullable=True)
    related_reference: Mapped[str | None] = mapped_column(String(60), nullable=True)
    assigned_staff_id: Mapped[str | None] = mapped_column(ForeignKey("users.user_code"), nullable=True)
    staff_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)                         # random hex, not guessable
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_code"), index=True)
    title: Mapped[str] = mapped_column(String(160), default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    messages: Mapped[list["ConversationMessage"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(12))                                         # user | assistant
    message_content: Mapped[str] = mapped_column(Text)                                    # already redacted
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(24), nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)              # sources, tools, approval/ticket refs
    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    __table_args__ = (CheckConstraint("role in ('user','assistant')", name="ck_msg_role"),)


class Memory(Base):
    __tablename__ = "memories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_code"), index=True)
    category: Mapped[str] = mapped_column(String(24))                                     # preference | profile | support_context | interest | plan
    memory_key: Mapped[str] = mapped_column(String(40))
    memory_value: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(16), default="chat")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    __table_args__ = (Index("ix_mem_user_key", "user_id", "memory_key"),)


class PolicyDocument(Base):
    __tablename__ = "policy_documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_name: Mapped[str] = mapped_column(String(160))
    version: Mapped[str] = mapped_column(String(32))
    source_type: Mapped[str] = mapped_column(String(32))                                  # structured_kb | generic_text
    source_path: Mapped[str] = mapped_column(String(300))
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    chunks: Mapped[list["PolicyChunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class PolicyChunk(Base):
    __tablename__ = "policy_chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("policy_documents.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[str] = mapped_column(String(32))                                     # e.g. F.3.2
    chunk_index: Mapped[int] = mapped_column(Integer)
    section_id: Mapped[str] = mapped_column(String(32), default="")
    section_title: Mapped[str] = mapped_column(String(200), default="")
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(240))
    chunk_type: Mapped[str] = mapped_column(String(24), default="rule")
    status: Mapped[str] = mapped_column(String(20), default="OFFICIAL")
    applies_to: Mapped[str] = mapped_column(String(120), default="")
    source: Mapped[str] = mapped_column(String(400), default="")
    questions: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    keywords: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    legacy_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    chunk_text: Mapped[str] = mapped_column(Text)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    document: Mapped[PolicyDocument] = relationship(back_populates="chunks")
    __table_args__ = (UniqueConstraint("document_id", "chunk_id", name="uq_chunk_doc"),)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_number: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.user_code"), index=True)
    action_type: Mapped[str] = mapped_column(String(32), index=True)
    target_resource_type: Mapped[str] = mapped_column(String(32))
    target_resource_id: Mapped[str] = mapped_column(String(64))
    proposed_change: Mapped[dict[str, Any]] = mapped_column(JSON)
    request_reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)        # PENDING APPROVED REJECTED EXECUTED FAILED EXPIRED
    proposal_hash: Mapped[str] = mapped_column(String(64))
    approver_id: Mapped[str | None] = mapped_column(ForeignKey("users.user_code"), nullable=True)
    approval_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(24), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    __table_args__ = (CheckConstraint("status in ('PENDING','APPROVED','REJECTED','EXECUTED','FAILED','EXPIRED')", name="ck_apr_status"),)


class AgentTrace(Base):
    __tablename__ = "agent_traces"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_code"), index=True)
    provider: Mapped[str] = mapped_column(String(16))
    model_name: Mapped[str] = mapped_column(String(80))
    tools_used: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    retrieval_references: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    memory_references: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    steps: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    tokens_estimated: Mapped[bool] = mapped_column(Boolean, default=False)                # True = local estimate, NOT provider-reported
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    model_latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    tool_latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[str] = mapped_column(String(24), index=True)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(24), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(24), unique=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    actor_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)   # string, not FK: failed logins name unknown users
    actor_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    authorization_decision: Mapped[str | None] = mapped_column(String(12), nullable=True)  # ALLOWED | DENIED | n/a
    approval_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    approver_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), index=True)                          # SUCCESS | FAILURE | DENIED | PENDING
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(24), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(24), nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)           # redacted, no PII values
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)          # HMAC of the cookie value; the raw token is never stored
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_code"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(200), nullable=True)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    ip: Mapped[str] = mapped_column(String(64), index=True)
    success: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    start_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date] = mapped_column(Date)
    title: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(12))                                       # OFFICIAL | TENTATIVE
    kb_ref: Mapped[str | None] = mapped_column(String(24), nullable=True)
