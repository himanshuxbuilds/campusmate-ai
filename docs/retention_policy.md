# Retention policy (demonstration defaults)

A reasonable starting policy for the demonstration; **it is not legal advice**. Adjust the periods to your institution's data-protection obligations. Periods are environment variables;
`python scripts/purge_retention.py` (cron / Task Scheduler) or `POST /api/admin/purge-retention` (admin) applies them and records an `ADMIN_ACTION` audit row.

| Data | Default | Variable | Notes |
|---|---|---|---|
| Conversation messages | 90 days | `RETENTION_MESSAGES_DAYS` | Stored already redacted (no secrets). Students can delete a conversation at any time. Empty conversations are removed |
| Agent traces | 90 days | `RETENTION_TRACES_DAYS` | Arguments redacted; no message text |
| Audit events | 365 days | `RETENTION_AUDIT_DAYS` | Append-only: there is no edit or delete API; only this age-based purge removes rows |
| Failed-login records | 30 days | `RETENTION_LOGIN_ATTEMPTS_DAYS` | Used for lockout (15-minute window) |
| Sessions | expired/revoked sessions removed after 7 days | (fixed) | Sessions live at most `SESSION_ABSOLUTE_MINUTES` (480) with a 30-minute idle timeout |
| Memory | `support_context` 90 days, `plan` 180 days, others until deleted | (per category in `memory_service`) | Reviewable and deletable by the student; expired items purged |
| Approval requests, tickets, student records | not purged | | Business records; manage under your records policy |

Never stored at any retention: passwords or OTPs (masked before processing), API keys, tokens, card or account numbers (only the last 4 digits of a bank account, only after approval).
