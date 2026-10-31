# Data model

Generated from the SQLAlchemy models by `scripts/generate_docs.py` (migrations: `migrations/`). Foreign keys point at `users.user_code`.

## `agent_traces`

| Column | Type | Constraints |
|---|---|---|
| id | INTEGER | PK |
| trace_id | VARCHAR(24) | NOT NULL, UNIQUE, INDEX |
| conversation_id | VARCHAR(32) | INDEX |
| user_id | VARCHAR(64) | NOT NULL, INDEX, FK users.user_code |
| provider | VARCHAR(16) | NOT NULL |
| model_name | VARCHAR(80) | NOT NULL |
| tools_used | JSON |  |
| retrieval_references | JSON |  |
| memory_references | JSON |  |
| steps | JSON |  |
| iterations | INTEGER | NOT NULL |
| input_tokens | INTEGER | NOT NULL |
| output_tokens | INTEGER | NOT NULL |
| tokens_estimated | BOOLEAN | NOT NULL |
| latency_ms | INTEGER | NOT NULL |
| model_latency_ms | INTEGER | NOT NULL |
| tool_latency_ms | INTEGER | NOT NULL |
| outcome | VARCHAR(24) | NOT NULL, INDEX |
| error_code | VARCHAR(40) |  |
| correlation_id | VARCHAR(24) |  |
| created_at | DATETIME | NOT NULL, INDEX |

## `approval_requests`

| Column | Type | Constraints |
|---|---|---|
| id | INTEGER | PK |
| request_number | VARCHAR(24) | NOT NULL, UNIQUE, INDEX |
| requester_id | VARCHAR(64) | NOT NULL, INDEX, FK users.user_code |
| action_type | VARCHAR(32) | NOT NULL, INDEX |
| target_resource_type | VARCHAR(32) | NOT NULL |
| target_resource_id | VARCHAR(64) | NOT NULL |
| proposed_change | JSON | NOT NULL |
| request_reason | TEXT | NOT NULL |
| status | VARCHAR(16) | NOT NULL, INDEX |
| proposal_hash | VARCHAR(64) | NOT NULL |
| approver_id | VARCHAR(64) | FK users.user_code |
| approval_comment | TEXT |  |
| execution_result | TEXT |  |
| trace_id | VARCHAR(24) |  |
| created_at | DATETIME | NOT NULL, INDEX |
| expires_at | DATETIME | NOT NULL |
| decided_at | DATETIME |  |
| executed_at | DATETIME |  |

Table constraints: `ck_apr_status`

## `audit_events`

| Column | Type | Constraints |
|---|---|---|
| id | INTEGER | PK |
| event_id | VARCHAR(24) | NOT NULL, UNIQUE |
| event_type | VARCHAR(32) | NOT NULL, INDEX |
| actor_id | VARCHAR(64) | INDEX |
| actor_role | VARCHAR(16) |  |
| action | VARCHAR(64) | NOT NULL |
| target_type | VARCHAR(32) |  |
| target_id | VARCHAR(64) |  |
| authorization_decision | VARCHAR(12) |  |
| approval_status | VARCHAR(16) |  |
| approver_id | VARCHAR(64) |  |
| outcome | VARCHAR(16) | NOT NULL, INDEX |
| reason | VARCHAR(200) |  |
| trace_id | VARCHAR(24) |  |
| correlation_id | VARCHAR(24) |  |
| details | JSON |  |
| created_at | DATETIME | NOT NULL, INDEX |

## `auth_sessions`

| Column | Type | Constraints |
|---|---|---|
| id | INTEGER | PK |
| token_hash | VARCHAR(64) | NOT NULL, UNIQUE, INDEX |
| user_id | VARCHAR(64) | NOT NULL, INDEX, FK users.user_code |
| csrf_token | VARCHAR(64) | NOT NULL |
| created_at | DATETIME | NOT NULL |
| last_seen_at | DATETIME | NOT NULL |
| expires_at | DATETIME | NOT NULL |
| revoked_at | DATETIME |  |
| ip | VARCHAR(64) |  |
| user_agent | VARCHAR(200) |  |

## `calendar_events`

| Column | Type | Constraints |
|---|---|---|
| id | INTEGER | PK |
| start_date | DATE | NOT NULL, INDEX |
| end_date | DATE | NOT NULL |
| title | VARCHAR(200) | NOT NULL |
| event_type | VARCHAR(20) | NOT NULL |
| status | VARCHAR(12) | NOT NULL |
| kb_ref | VARCHAR(24) |  |

## `conversation_messages`

| Column | Type | Constraints |
|---|---|---|
| id | INTEGER | PK |
| conversation_id | VARCHAR(32) | NOT NULL, INDEX, FK conversations.id |
| role | VARCHAR(12) | NOT NULL |
| message_content | TEXT | NOT NULL |
| created_at | DATETIME | NOT NULL, INDEX |
| trace_id | VARCHAR(24) |  |
| meta | JSON |  |

Table constraints: `ck_msg_role`

## `conversations`

| Column | Type | Constraints |
|---|---|---|
| id | VARCHAR(32) | PK |
| user_id | VARCHAR(64) | NOT NULL, INDEX, FK users.user_code |
| title | VARCHAR(160) | NOT NULL |
| created_at | DATETIME | NOT NULL |
| updated_at | DATETIME | NOT NULL, INDEX |
| deleted_at | DATETIME |  |

## `course_registrations`

| Column | Type | Constraints |
|---|---|---|
| id | INTEGER | PK |
| student_id | VARCHAR(64) | NOT NULL, INDEX, FK users.user_code |
| course_name | VARCHAR(160) | NOT NULL |
| trimester | VARCHAR(8) | NOT NULL |
| kind | VARCHAR(16) | NOT NULL |
| credits | INTEGER | NOT NULL |
| status | VARCHAR(16) | NOT NULL |
| updated_at | DATETIME | NOT NULL |

## `fee_installments`

| Column | Type | Constraints |
|---|---|---|
| id | INTEGER | PK |
| student_id | VARCHAR(64) | NOT NULL, INDEX, FK users.user_code |
| name | VARCHAR(60) | NOT NULL |
| due_date | DATE | NOT NULL |
| amount | INTEGER | NOT NULL |
| status | VARCHAR(12) | NOT NULL |
| paid_on | DATE |  |
| late_fee | INTEGER |  |
| late_fee_paid | BOOLEAN | NOT NULL |
| note | VARCHAR(300) |  |

Table constraints: `ck_inst_amount`

## `fee_records`

| Column | Type | Constraints |
|---|---|---|
| id | INTEGER | PK |
| student_id | VARCHAR(64) | NOT NULL, INDEX, FK users.user_code |
| academic_year | VARCHAR(16) | NOT NULL |
| total_tuition | INTEGER | NOT NULL |
| caution_deposit | INTEGER | NOT NULL |
| scholarship_adjustment_total | INTEGER | NOT NULL |
| total_fee | INTEGER | NOT NULL |
| amount_paid | INTEGER | NOT NULL |
| amount_due | INTEGER | NOT NULL |
| status | VARCHAR(20) | NOT NULL |
| updated_at | DATETIME | NOT NULL |

Table constraints: `ck_fee_nonneg`, `uq_fee_student_year`

## `login_attempts`

| Column | Type | Constraints |
|---|---|---|
| id | INTEGER | PK |
| username | VARCHAR(64) | NOT NULL, INDEX |
| ip | VARCHAR(64) | NOT NULL, INDEX |
| success | BOOLEAN | NOT NULL |
| created_at | DATETIME | NOT NULL, INDEX |

## `memories`

| Column | Type | Constraints |
|---|---|---|
| id | INTEGER | PK |
| user_id | VARCHAR(64) | NOT NULL, INDEX, FK users.user_code |
| category | VARCHAR(24) | NOT NULL |
| memory_key | VARCHAR(40) | NOT NULL |
| memory_value | VARCHAR(200) | NOT NULL |
| source | VARCHAR(16) | NOT NULL |
| created_at | DATETIME | NOT NULL |
| updated_at | DATETIME | NOT NULL |
| expires_at | DATETIME |  |

## `policy_chunks`

| Column | Type | Constraints |
|---|---|---|
| id | INTEGER | PK |
| document_id | INTEGER | NOT NULL, INDEX, FK policy_documents.id |
| chunk_id | VARCHAR(32) | NOT NULL |
| chunk_index | INTEGER | NOT NULL |
| section_id | VARCHAR(32) | NOT NULL |
| section_title | VARCHAR(200) | NOT NULL |
| page_number | INTEGER |  |
| title | VARCHAR(240) | NOT NULL |
| chunk_type | VARCHAR(24) | NOT NULL |
| status | VARCHAR(20) | NOT NULL |
| applies_to | VARCHAR(120) | NOT NULL |
| source | VARCHAR(400) | NOT NULL |
| questions | JSON |  |
| keywords | JSON |  |
| legacy_ids | JSON |  |
| chunk_text | TEXT | NOT NULL |
| word_count | INTEGER | NOT NULL |

Table constraints: `uq_chunk_doc`

## `policy_documents`

| Column | Type | Constraints |
|---|---|---|
| id | INTEGER | PK |
| document_name | VARCHAR(160) | NOT NULL |
| version | VARCHAR(32) | NOT NULL |
| source_type | VARCHAR(32) | NOT NULL |
| source_path | VARCHAR(300) | NOT NULL |
| content_hash | VARCHAR(64) | NOT NULL |
| created_at | DATETIME | NOT NULL |

## `scholarship_records`

| Column | Type | Constraints |
|---|---|---|
| id | INTEGER | PK |
| student_id | VARCHAR(64) | NOT NULL, INDEX, FK users.user_code |
| scholarship_name | VARCHAR(160) |  |
| application_status | VARCHAR(40) | NOT NULL |
| academic_year | VARCHAR(16) | NOT NULL |
| amount_total | INTEGER |  |
| amount_adjusted | INTEGER |  |
| amount_remaining | INTEGER |  |
| notes | TEXT |  |
| applied_on | DATE |  |
| decided_on | DATE |  |
| updated_at | DATETIME | NOT NULL |

## `student_records`

| Column | Type | Constraints |
|---|---|---|
| id | INTEGER | PK |
| student_id | VARCHAR(64) | NOT NULL, INDEX, FK users.user_code |
| academic_year | VARCHAR(16) | NOT NULL |
| programme | VARCHAR(32) | NOT NULL |
| programme_name | VARCHAR(120) | NOT NULL |
| batch | VARCHAR(16) | NOT NULL |
| current_trimester | VARCHAR(8) | NOT NULL |
| year1_cgpa | FLOAT |  |
| attendance_pct | INTEGER |  |
| hosteller | BOOLEAN | NOT NULL |
| address | VARCHAR(300) |  |
| mobile | VARCHAR(32) |  |
| refund_bank_masked | VARCHAR(80) |  |
| created_at | DATETIME | NOT NULL |
| updated_at | DATETIME | NOT NULL |

Table constraints: `uq_student_year`

## `support_tickets`

| Column | Type | Constraints |
|---|---|---|
| id | INTEGER | PK |
| ticket_number | VARCHAR(24) | NOT NULL, UNIQUE, INDEX |
| student_id | VARCHAR(64) | NOT NULL, INDEX, FK users.user_code |
| category | VARCHAR(32) | NOT NULL, INDEX |
| subject | VARCHAR(160) | NOT NULL |
| description | TEXT | NOT NULL |
| priority | VARCHAR(10) | NOT NULL |
| status | VARCHAR(20) | NOT NULL, INDEX |
| programme | VARCHAR(32) |  |
| related_reference | VARCHAR(60) |  |
| assigned_staff_id | VARCHAR(64) | FK users.user_code |
| staff_note | TEXT |  |
| created_at | DATETIME | NOT NULL, INDEX |
| updated_at | DATETIME | NOT NULL |

## `users`

| Column | Type | Constraints |
|---|---|---|
| id | INTEGER | PK |
| user_code | VARCHAR(64) | NOT NULL, UNIQUE, INDEX |
| name | VARCHAR(120) | NOT NULL |
| email | VARCHAR(160) | UNIQUE |
| password_hash | VARCHAR(300) | NOT NULL |
| role | VARCHAR(16) | NOT NULL, INDEX |
| programme | VARCHAR(32) |  |
| is_active | BOOLEAN | NOT NULL |
| staff_scope | JSON |  |
| created_at | DATETIME | NOT NULL |
| updated_at | DATETIME | NOT NULL |

Table constraints: `ck_users_role`
